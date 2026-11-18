"""Registries across checks (places, people and apps) and the changes feed."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.checks import ScopeLevel
from app.db.types import UTCDateTime


class Scope(Base):
    """One row per ARM scope ever walked by a completed scan.

    The *current* view of a scope — "what does prod look like today" —
    is ``latest_scan_id``: the most recently completed scan whose walk
    included this node. Because an MG-rooted scan walks every
    subscription and resource group below it, a resource group's
    pointer can reference an MG scan, a subscription scan, or an RG
    scan, whichever finished last. Parent links give breadcrumbs and
    the tree without parsing ``scans.hierarchy`` JSONB on every request.
    """

    __tablename__ = "scopes"

    # Lowercased ARM path, same normalisation as ``role_assignments.scope``.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    level: Mapped[ScopeLevel] = mapped_column(String(20), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("scopes.id", ondelete="SET NULL"), index=True
    )
    # ``microsoft.storage/storageaccounts`` for resources; null above that.
    resource_type: Mapped[str | None] = mapped_column(String(255))
    # Azure tags on subscriptions and resource groups, from the newest walk.
    tags: Mapped[dict[str, Any] | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    latest_scan_id: Mapped[int | None] = mapped_column(
        ForeignKey("scans.id", ondelete="SET NULL"), index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class Principal(Base):
    """One row per Entra object id seen in any completed scan (as an RA
    principal or a group member).

    Names are best-effort from Graph; ``display_name`` is kept from the
    most recent scan that *could* resolve it, so a transient Graph
    failure on one run does not blank a name we already know.
    ``last_activity_at`` is the newest Activity Log event the coverage
    builder attributed to this principal.
    """

    __tablename__ = "principals"

    # Lowercased object id.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(500))
    principal_type: Mapped[str | None] = mapped_column(String(50), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_activity_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Service principals: the app's publishing tenant and kind (see RoleAssignment).
    app_owner_org: Mapped[str | None] = mapped_column(String(64))
    sp_type: Mapped[str | None] = mapped_column(String(30))


class AccessChange(Base):
    """One change to who can do what: a grant given or removed, a PIM
    eligibility added or removed, or someone joining or leaving a group
    that holds access.

    Found by comparing each completed check with the previous check of
    the same place. ``source`` says how exact the time and author are:
    ``activity_log`` (the matching Azure event), ``created_on`` (Azure's
    own ``createdOn`` / ``createdBy`` on the grant), ``entra_audit`` (the
    Entra ID audit log, for someone joining or leaving a group), or
    ``daily_check`` (first seen by the check; no author).
    """

    __tablename__ = "access_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    first_seen_scan_id: Mapped[int | None] = mapped_column(
        ForeignKey("scans.id", ondelete="SET NULL"), index=True
    )
    # granted | removed | eligible_added | eligible_removed | member_added | member_removed
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_name: Mapped[str | None] = mapped_column(Text)
    scope_level: Mapped[str] = mapped_column(String(20), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    principal_name: Mapped[str | None] = mapped_column(String(500))
    principal_type: Mapped[str | None] = mapped_column(String(50))
    role_definition_id: Mapped[str | None] = mapped_column(Text)
    role_name: Mapped[str | None] = mapped_column(String(255))
    # Capability level of the role (or, for membership, the group's strongest).
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    # Membership changes: the group joined or left.
    via_group_id: Mapped[str | None] = mapped_column(String(255))
    via_group_name: Mapped[str | None] = mapped_column(String(500))
    actor_id: Mapped[str | None] = mapped_column(String(255))
    actor_name: Mapped[str | None] = mapped_column(String(500))
    # ``app`` when Entra records an app as the author (Teams or Outlook for
    # Microsoft 365 groups, access packages, PIM, the AD sync), ``user`` for a
    # person; set only from the Entra ID audit log.
    actor_type: Mapped[str | None] = mapped_column(String(10))
    # Membership changes from the audit log: the group the member was added
    # to or removed from, when it is a group nested inside ``via_group``.
    direct_group_name: Mapped[str | None] = mapped_column(String(500))
    # Azure's role assignment / eligibility schedule id, to match late Activity Log events.
    azure_id: Mapped[str | None] = mapped_column(Text)
    # Indexed: deleting an Activity Log row looks up the changes pointing at it.
    activity_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("activity_events.id", ondelete="SET NULL"), index=True
    )
    # Stable subject: the grant fingerprint for grants and eligibilities,
    # ``member:<group>:<member>`` for memberships.
    subject_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("uq_access_changes_subject", "subject_key", "kind", "occurred_at", unique=True),
        CheckConstraint(
            "kind IN ('granted', 'removed', 'eligible_added', 'eligible_removed', "
            "'member_added', 'member_removed')",
            name="ck_access_changes_kind",
        ),
        CheckConstraint(
            "source IN ('activity_log', 'created_on', 'daily_check', 'entra_audit')",
            name="ck_access_changes_source",
        ),
    )
