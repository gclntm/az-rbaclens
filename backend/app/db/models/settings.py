"""Settings and operations: credentials, sign-in, app settings, schedules and the audit log."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class AzureCredentials(Base):
    """Singleton row (id=1) holding Azure SP credentials entered via the
    settings page. The client_secret is encrypted with the Fernet key in
    ``settings.encryption_key``; tenant_id and client_id are non-secret
    public identifiers and stored as plain text.
    """

    __tablename__ = "azure_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(255))
    client_id: Mapped[str | None] = mapped_column(String(255))
    client_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuthSettings(Base):
    """Singleton row (id=1) for the Entra ID / Azure AD sign-in config.

    All fields are public identifiers (no secrets) — the SPA reads them
    via the unauthenticated ``/api/auth/config`` endpoint to bootstrap
    MSAL. ``enabled`` is the only on/off knob; when false the backend's
    auth dependency is a no-op so the app behaves exactly like before.

    ``allowed_group_ids`` is a comma-separated list of Entra group object
    IDs. When set, the access token must contain at least one of them in
    its ``groups`` claim (which requires the app registration to emit
    group claims). Leave empty to accept any signed-in user from the
    configured tenant.
    """

    __tablename__ = "auth_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String(255))
    client_id: Mapped[str | None] = mapped_column(String(255))
    allowed_group_ids: Mapped[str | None] = mapped_column(Text)
    # When set, only users whose access-token ``groups`` claim contains
    # one of these Entra group object IDs can hit admin endpoints
    # (``/api/settings/*``). Empty = every authenticated user is admin
    # (backward-compatible default for single-team deployments).
    admin_group_ids: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AppSetting(Base):
    """Small key / value store for app-wide state that is not a secret,
    e.g. whether the default schedules were created and which of them
    an admin removed on purpose."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class JobSchedule(Base):
    """A recurring job: a snapshot of a scope, or an Activity Log pull for
    a subscription. The scheduler loop starts it when ``next_run_at`` is
    due and stamps ``last_*``; the job itself is an ordinary ``scans`` /
    ``activity_pulls`` row.
    """

    __tablename__ = "job_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # snapshot | activity_pull
    # Snapshot: normalised ARM scope. Pull: lowercased subscription GUID.
    target: Mapped[str] = mapped_column(Text, nullable=False)
    every_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    # Daily schedules only: the hour (container local time) each run is
    # aligned to, e.g. 6 for the nightly whole-tenant check. Null = plain interval.
    at_hour: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Pulls only: lookback for the first run (later runs are incremental).
    initial_window_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_job_id: Mapped[int | None] = mapped_column(Integer)
    # Why the last attempt did not start a job (e.g. one already in flight), or
    # why the last job failed; kept until a run succeeds.
    last_error: Mapped[str | None] = mapped_column(Text)
    # Failed runs in a row: sets how soon the next try comes (core/scheduler.py).
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    # The last job whose outcome ``settle`` recorded here.
    settled_job_id: Mapped[int | None] = mapped_column(Integer)
    # Pulls only: end of the newest window this schedule collected. The next
    # run starts here, whatever manual pulls did in between.
    covered_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # When it was switched off, and by whom (the signed-in name; empty while
    # sign-in is off). Both cleared on resume.
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    paused_by: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_job_schedules_kind_target", "kind", "target", unique=True),
        Index("ix_job_schedules_due", "enabled", "next_run_at"),
        CheckConstraint("kind IN ('snapshot', 'activity_pull')", name="ck_job_schedules_kind"),
        CheckConstraint("every_hours BETWEEN 1 AND 720", name="ck_job_schedules_every"),
        CheckConstraint(
            "at_hour IS NULL OR (at_hour BETWEEN 0 AND 23 AND every_hours = 24)",
            name="ck_job_schedules_at_hour",
        ),
    )


class AuditEvent(Base):
    """One row per mutating API call.

    Captures *who* (from the JWT principal id / name when auth is on,
    ``anonymous`` otherwise) did *what* (HTTP method + path), *when* and
    *from where* (``client_ip``), plus the response status so failed
    attempts are visible too. Body
    contents are deliberately *not* recorded — they may contain secrets
    (e.g. client_secret payloads on the credentials endpoint).
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    actor: Mapped[str | None] = mapped_column(String(255), index=True)
    actor_name: Mapped[str | None] = mapped_column(String(500))
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    # The caller's address as the app sees it (core/audit.py); empty on rows before 0.13.
    client_ip: Mapped[str | None] = mapped_column(String(45))
    # What the change changed, when the endpoint says so (``request.state.audit_detail``,
    # 0042): never the request body.
    detail: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
