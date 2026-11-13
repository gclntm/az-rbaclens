"""Checks: a snapshot of grants, the groups and roles it read, and PIM eligibilities."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime

ScanStatus = str  # "pending" | "running" | "done" | "failed"


ScanStatus = str  # "pending" | "running" | "done" | "failed"
ScopeLevel = str  # "mgmt_group" | "subscription" | "resource_group" | "resource"


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_level: Mapped[ScopeLevel] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[ScanStatus] = mapped_column(String(20), default="pending", index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error: Mapped[str | None] = mapped_column(Text)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    progress_done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(Text)
    # Bumped on every progress update by the worker. The sweeper treats a
    # job as stuck when this (not ``started_at``) is older than the
    # timeout, so a legitimately long scan that is still making progress
    # is never flipped to ``failed`` under a live worker.
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # ``hierarchy`` captures the scope tree walked during the scan so the
    # /tree endpoint can render parent → child nesting (incl. ancestors
    # reached via inheritance). Shape: ``{scope: {name, level, parent}}``.
    hierarchy: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    # PIM (Privileged Identity Management) for this snapshot: ``read`` when
    # eligible assignments were listed (possibly none), ``failed`` with
    # ``eligibility_error`` when they couldn't be, NULL for snapshots taken
    # before 0024. Lets the UI say "nobody is eligible" only when checked.
    eligibility_status: Mapped[str | None] = mapped_column(String(20))
    eligibility_error: Mapped[str | None] = mapped_column(Text)
    # ``schedule`` or ``manual``; NULL before 0037, when it was not recorded.
    origin: Mapped[str | None] = mapped_column(String(10))
    # The schedule that started this scan. Not an FK: deleting a schedule keeps its jobs.
    schedule_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), nullable=False
    )

    # ``passive_deletes``: the FK already cascades at the DB level, so the
    # ORM must not load every child row (incl. ``raw``) just to emit
    # per-row DELETEs when a scan is removed.
    role_assignments: Mapped[list["RoleAssignment"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # At most one in-flight scan per scope. Two concurrent scans of the
        # same root would race on ``group_memberships`` uniqueness and
        # produce two "latest" candidates; the API turns the violation
        # into a 409.
        Index(
            "uq_scans_inflight_scope",
            "scope",
            unique=True,
            postgresql_where=text("status IN ('pending','running')"),
            sqlite_where=text("status IN ('pending','running')"),
        ),
    )


class RoleAssignment(Base):
    __tablename__ = "role_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_name: Mapped[str | None] = mapped_column(Text)
    scope_level: Mapped[ScopeLevel] = mapped_column(String(20), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    principal_name: Mapped[str | None] = mapped_column(String(500))
    principal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Service principals, from Graph: the tenant that publishes the app
    # (``appOwnerOrganizationId``, lowercased; Microsoft's own apps come
    # from its tenants) and ``servicePrincipalType`` (Application,
    # ManagedIdentity, …). NULL for people and groups, or when Graph
    # wasn't read.
    principal_app_owner: Mapped[str | None] = mapped_column(String(64))
    principal_sp_type: Mapped[str | None] = mapped_column(String(30))
    role_name: Mapped[str | None] = mapped_column(String(255))
    role_definition_id: Mapped[str] = mapped_column(Text, nullable=False)
    # ``BuiltInRole`` | ``CustomRole`` (Azure's classification) or NULL if
    # the role_definitions lookup failed. Lets the UI highlight custom
    # roles, which usually warrant more scrutiny in an access review.
    role_type: Mapped[str | None] = mapped_column(String(20))
    # ABAC condition narrowing this assignment (e.g. "may assign any role
    # except Owner"), verbatim from Azure; see core/conditions.py.
    condition: Mapped[str | None] = mapped_column(Text)
    condition_version: Mapped[str | None] = mapped_column(String(10))
    # From PIM's active schedule instances: ``Assigned`` (standing) or
    # ``Activated`` (an eligible role switched on), and when it ends —
    # NULL ``end_at`` = permanent. Both NULL when PIM wasn't read.
    assignment_type: Mapped[str | None] = mapped_column(String(20))
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Who gave the grant and when, as Azure records it (``createdOn`` /
    # ``createdBy``; ``created_by`` lowercased). The name and type are
    # resolved at ingest — often a pipeline service principal.
    created_on: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_by_name: Mapped[str | None] = mapped_column(String(500))
    created_by_type: Mapped[str | None] = mapped_column(String(50))
    updated_on: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Azure's own record of the grant: read only by the check that saves it, so not
    # loaded with the row (it adds up across a tenant's checks).
    raw: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), deferred=True
    )

    scan: Mapped[Scan] = relationship(back_populates="role_assignments")

    __table_args__ = (
        Index("ix_role_assignments_scan_scope", "scan_id", "scope"),
        Index("ix_role_assignments_scan_role", "scan_id", "role_name"),
        Index("ix_role_assignments_scan_principal", "scan_id", "principal_id"),
    )


class ScanGroupResolution(Base):
    """Records whether we *successfully* fetched a group's transitive
    membership during a scan — independent of how many members it has.

    Without this, an empty security group (a real Azure scenario:
    role granted to a group that has zero members at scan time) is
    indistinguishable from a Graph permission failure: both produce
    zero ``group_memberships`` rows. The Coverage tab would then
    render both as "membership unresolved", which is wrong for the
    empty case.

    Row present → resolved (possibly empty).
    Row absent  → unresolved (Graph 403, transient failure, or
                   we never queried).
    """

    __tablename__ = "scan_group_resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True, nullable=False
    )
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index(
            "uq_scan_group_resolutions_scan_group",
            "scan_id",
            "group_id",
            unique=True,
        ),
    )


class GroupMembership(Base):
    """One row per (scan, group, member) — the transitive membership
    of every group-typed principal that holds a role in the scan.

    Phase 5 of the events / least-privilege feature. A group's role
    assignments cascade to its members (including SP members of
    security groups), but Activity Log captures the *executing*
    identity, not the group it inherited via. Without this mapping
    the coverage analyzer would read every group-typed RA as 0 %
    even when the group is being actively used.

    Resolved via Microsoft Graph ``/groups/{id}/transitiveMembers``,
    which already flattens nested groups for us. Scoped to the scan
    so re-running picks up membership changes between scans.

    ``member_name`` is best-effort — the same Graph permission gate
    as ``role_assignments.principal_name``: missing
    ``Group.Read.All`` / ``User.Read.All`` etc. leaves the column
    null and the coverage code falls back to the bare GUID.
    """

    __tablename__ = "group_memberships"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), index=True, nullable=False
    )
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False)
    member_type: Mapped[str | None] = mapped_column(String(50))
    member_name: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        Index(
            "uq_group_memberships_scan_group_member",
            "scan_id",
            "group_id",
            "member_id",
            unique=True,
        ),
        Index("ix_group_memberships_scan_group", "scan_id", "group_id"),
        Index("ix_group_memberships_scan_member", "scan_id", "member_id"),
    )


class RoleDefinition(Base):
    """Cached copy of an Azure role definition.

    Keyed by the role definition's GUID (the trailing segment of the
    full ARM id ``/subscriptions/<sub>/providers/Microsoft.Authorization
    /roleDefinitions/<guid>`` or
    ``/providers/Microsoft.Authorization/roleDefinitions/<guid>`` for
    built-ins). The Azure RBAC catalogue is global per definition, so
    one row serves every scan that referenced this role — no per-scan
    duplication, and the data survives scan deletion.

    ``role_name`` and ``role_type`` mirror what we already store on
    ``role_assignments``; we keep them here too so a reviewer browsing
    the role detail card doesn't need to round-trip through an RA. The
    interesting columns are ``description`` (built-in roles ship with
    one), ``permissions`` (the full actions/notActions/dataActions/
    notDataActions structure), and ``assignable_scopes`` (where Azure
    will *let* this role be assigned — different from where it actually
    *is* assigned).
    """

    __tablename__ = "role_definitions"

    # Bare role-definition GUID, lowercased. We strip the ``/subscriptions/
    # <sub>/`` prefix because the same GUID identifies the role across
    # every scope it appears at — keeping the prefix would create
    # duplicate rows for the same built-in.
    role_definition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role_name: Mapped[str | None] = mapped_column(String(255))
    role_type: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    # Shape: ``[{actions: [...], notActions: [...], dataActions: [...],
    # notDataActions: [...]}, ...]`` (Azure allows a list, in practice
    # built-ins have one entry; custom roles usually too).
    permissions: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    assignable_scopes: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    fetched_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RoleEligibility(Base):
    """A PIM *eligible* assignment seen by a snapshot: the principal can
    activate the role on demand (often with approval / MFA / a time limit)
    but doesn't hold it until then — so it never appears in
    ``role_assignments``. Same scope / principal / role shape.
    """

    __tablename__ = "role_eligibilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Azure's eligibility schedule instance id (dedupe key within a scan).
    azure_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    scope_name: Mapped[str | None] = mapped_column(Text)
    scope_level: Mapped[ScopeLevel] = mapped_column(String(20), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    principal_name: Mapped[str | None] = mapped_column(String(500))
    principal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    role_definition_id: Mapped[str] = mapped_column(Text, nullable=False)
    role_name: Mapped[str | None] = mapped_column(String(255))
    role_type: Mapped[str | None] = mapped_column(String(20))
    # ``Direct`` | ``Group`` (eligible through a group) | ``Inherited``.
    member_type: Mapped[str | None] = mapped_column(String(20))
    condition: Mapped[str | None] = mapped_column(Text)
    condition_version: Mapped[str | None] = mapped_column(String(10))
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # NULL = eligible with no end date.
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str | None] = mapped_column(String(30))

    __table_args__ = (
        Index("uq_role_eligibilities_scan_azure", "scan_id", "azure_id", unique=True),
        Index("ix_role_eligibilities_scan_scope", "scan_id", "scope"),
        Index("ix_role_eligibilities_scan_principal", "scan_id", "principal_id"),
    )
