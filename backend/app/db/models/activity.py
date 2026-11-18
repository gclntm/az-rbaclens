"""The Activity Log: collections, events, compacted days and operation names."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity_payload import caller_oid_from_payload
from app.db.base import Base
from app.db.types import UTCDateTime


class ActivityPull(Base):
    """One pull of Azure Activity Log events from a subscription.

    Records the *attempt* (subscription + time window + status), not
    the events themselves. Events live in ``activity_events`` and
    survive pull deletion since the same Azure ``event_data_id`` can
    appear across overlapping pulls. A pull is metadata: when we
    asked Azure, what window, did it succeed, how many fresh events
    did we end up persisting after dedupe.
    """

    __tablename__ = "activity_pulls"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)
    # Rows newly inserted by this pull (existing event_data_ids are
    # skipped). ``events_seen`` is the total Azure returned, useful
    # for spotting silent overlap with prior pulls.
    events_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_persisted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(Text)
    # See ``Scan.heartbeat_at``.
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # ``schedule`` or ``manual``, and the schedule (see ``Scan.origin``).
    origin: Mapped[str | None] = mapped_column(String(10))
    schedule_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # One in-flight pull per subscription: two concurrent pulls both
        # load the dedupe set, then both insert the same event_data_id.
        Index(
            "uq_activity_pulls_inflight_subscription",
            "subscription_id",
            unique=True,
            postgresql_where=text("status IN ('pending','running')"),
            sqlite_where=text("status IN ('pending','running')"),
        ),
    )


class ActivityEvent(Base):
    """One Azure Activity Log entry.

    Keyed by ``(subscription_id, event_data_id)`` so re-pulling an
    overlapping window is idempotent: existing rows are skipped, not
    duplicated. Not FK'd to a pull on purpose — events outlive the
    pull that fetched them.
    """

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Azure's unique-per-event id (``eventDataId``). UUID-shaped.
    event_data_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # The principal that drove the operation. For users this is the
    # UPN or display name; for SPs / managed identities it's a GUID
    # or the SP's display name depending on what Azure had cached.
    caller: Mapped[str | None] = mapped_column(String(500))
    # Heuristic classification (``User`` | ``ServicePrincipal`` |
    # ``ManagedIdentity`` | null). Derived from the JWT ``idtyp``
    # claim or the shape of ``caller`` — Azure doesn't return a
    # definitive flag, so this is best-effort.
    caller_type: Mapped[str | None] = mapped_column(String(50))
    # ``operationName.value`` — the ARM action string, e.g.
    # ``Microsoft.Storage/storageAccounts/write``. This is what we
    # intersect against role definitions in Phase 3.
    operation_name: Mapped[str] = mapped_column(String(500), nullable=False)
    # Full ARM id of the affected resource. Empty for some
    # subscription-level operations.
    resource_id: Mapped[str | None] = mapped_column(Text)
    # ``Microsoft.Storage/storageAccounts``-style provider/type
    # extracted from the resource id; null for sub-level operations.
    resource_type: Mapped[str | None] = mapped_column(String(255))
    # ``Started`` | ``Accepted`` | ``Succeeded`` | ``Failed``. We
    # persist all four — a Failed attempt is also signal that a
    # principal *tried* an action.
    status: Mapped[str | None] = mapped_column(String(50), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    # The caller's object id from the JWT claims, lowercased — what ties an
    # event to a principal. Derived from ``raw`` on insert (listener below).
    caller_oid: Mapped[str | None] = mapped_column(String(64))
    # How many events this row stands for: 1 for an event kept in full; more
    # for a daily row, where machine activity of one day is folded into one
    # row per caller, operation, resource and status (core/activity_compaction.py).
    # Count with ``sum(event_count)``, never ``count(*)``.
    event_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    # Daily rows only: the first of their events (``occurred_at`` is the last).
    first_occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Slimmed SDK payload for the event drawer; see core/activity_payload.py.
    # Loaded only when read (one event's detail): lists of events skip it.
    raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), deferred=True
    )

    # No B-tree on ``caller`` / ``operation_name``: the filters are
    # ``lower(col) LIKE '%x%'``, which no B-tree serves (0022 dropped them).
    __table_args__ = (
        Index(
            "uq_activity_events_sub_event",
            "subscription_id",
            "event_data_id",
            unique=True,
        ),
        Index("ix_activity_events_sub_time", "subscription_id", "occurred_at"),
        Index("ix_activity_events_oid_time", "caller_oid", "occurred_at"),
    )


class ActivityCompactedDay(Base):
    """A day of one subscription (or of the management groups) whose machine
    activity was folded into daily rows. Collections skip those events on
    such a day: they are counted already (core/activity_compaction.py)."""

    __tablename__ = "activity_compacted_days"

    subscription_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    compacted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    events_before: Mapped[int] = mapped_column(Integer, nullable=False)
    rows_after: Mapped[int] = mapped_column(Integer, nullable=False)


@event.listens_for(ActivityEvent, "before_insert")
def _derive_caller_oid(_mapper, _connection, target: ActivityEvent) -> None:
    if target.caller_oid is None:
        target.caller_oid = caller_oid_from_payload(target.raw)


class OperationName(Base):
    """Display name of one ARM operation, from Azure's provider operations
    metadata (``Microsoft.Compute/virtualMachines/write`` → "Create or
    Update Virtual Machine"). Keyed lowercased: the Activity Log spells
    the same operation in more than one case.
    """

    __tablename__ = "operation_names"

    operation: Mapped[str] = mapped_column(Text, primary_key=True)
    # Lowercased namespace, e.g. ``microsoft.compute`` — refreshed per provider.
    provider: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type_display: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DirectoryAuditEvent(Base):
    """One group membership or Entra role event from the Entra ID audit log (tools/entra_audit.py).

    Who added or removed a member of a group, and when. The changes feed
    matches them to the members a check saw join or leave a group that
    holds access (``core/changes.py``). Ids are lowercased, as in
    ``group_memberships``. Kept as long as the Activity Log
    (``EVENT_RETENTION_DAYS``).
    """

    __tablename__ = "directory_audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Entra's own id of the audit entry: reading a window twice stores it once.
    audit_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # group | role | eligible | activation (0041) | owner (0043): a group's members,
    # an Entra role, an Entra role through PIM eligibility, a PIM activation, or
    # the owners of a group or an app (``group_id`` is then the object owned:
    # the group, the service principal, or ``appid:<client id>`` for a registration).
    kind: Mapped[str] = mapped_column(String(12), nullable=False, server_default="group")
    # add | remove
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    # success | failure | … as Entra says it
    result: Mapped[str | None] = mapped_column(String(20))
    group_id: Mapped[str | None] = mapped_column(String(64))
    group_name: Mapped[str | None] = mapped_column(String(500))
    # The Entra role (its template id, lowercased), for every kind but ``group``.
    role_id: Mapped[str | None] = mapped_column(String(64))
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_type: Mapped[str | None] = mapped_column(String(50))
    member_name: Mapped[str | None] = mapped_column(String(500))
    # user | app: who made the change. An app is how Entra records a change
    # made through a service (Teams or Outlook for Microsoft 365 groups,
    # access packages, PIM, the sync from on-premises AD).
    initiator_type: Mapped[str | None] = mapped_column(String(10))
    initiator_id: Mapped[str | None] = mapped_column(String(64))
    initiator_name: Mapped[str | None] = mapped_column(String(500))
    raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), deferred=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("audit_id", name="uq_directory_audit_events_audit_id"),)
