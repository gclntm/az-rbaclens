"""Scan-level coverage builder.

Moved out of the ``/scans/{id}/coverage`` request handler so it can run
at snapshot end (see :mod:`app.core.findings`) and be cached. The
request handler is now a thin wrapper: serve the cached result when
present, else build live.

Everything here is DB-reading only; ``app.core.role_coverage`` holds
the pure pattern logic.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.activity_targets import MGMT_GROUP_PREFIX, MGMT_GROUPS
from app.core.role_coverage import (
    compute_coverage,
    recommend_narrower_roles,
    role_covers,
)
from app.core.role_risk import classify_permissions
from app.core.timeutil import as_utc
from app.db.models import (
    ActivityEvent,
    GroupMembership,
    RoleAssignment,
    RoleDefinition,
    Scan,
    ScanGroupResolution,
)
from app.schemas.iam_access import (
    CoveredByRef,
    RoleCoverageResult,
    RoleCoverageRow,
    RoleRecommendation,
    TopOperation,
    UnattributedCaller,
)


def subscription_id_of_scope(scope: str) -> str | None:
    """Extract the subscription GUID from an ARM scope, if any.

    Works for ``/subscriptions/<id>``, ``/subscriptions/<id>/resourceGroups/…``
    and full resource paths. Returns None for mgmt-group / tenant-level
    scopes (which can't be queried directly via Activity Log; the caller
    has to walk down to the sub level themselves).
    """
    marker = "/subscriptions/"
    lower = scope.lower()
    idx = lower.find(marker)
    if idx < 0:
        return None
    rest = scope[idx + len(marker) :]
    guid = rest.split("/", 1)[0]
    return guid.lower() or None


def caller_oid_expr():
    """The caller's object id (lowercased) — the key that ties an Activity
    Log event to a principal id. A plain column since 0022 (it was a JSON
    path into ``raw``), indexed with ``occurred_at``."""
    return ActivityEvent.caller_oid


def _aggregated_events(
    session: Session, subscriptions: set[str], start: datetime, end: datetime
) -> list[tuple[str, str | None, str | None, str | None, str, str | None, int, datetime | None]]:
    """``(sub, caller, caller_type, oid, operation, resource, count, newest)`` groups.

    ``oid`` is the caller's object id (``activity_events.caller_oid``) — the
    most reliable correlation key. Grouped in a subquery so GROUP BY
    references plain columns on every dialect.
    """
    inner = (
        select(
            ActivityEvent.subscription_id.label("sub"),
            ActivityEvent.caller.label("caller"),
            ActivityEvent.caller_type.label("ctype"),
            ActivityEvent.caller_oid.label("oid"),
            ActivityEvent.operation_name.label("op"),
            func.lower(ActivityEvent.resource_id).label("res"),
            ActivityEvent.occurred_at.label("at"),
            ActivityEvent.event_count.label("n"),
        )
        .where(ActivityEvent.subscription_id.in_(subscriptions))
        .where(ActivityEvent.occurred_at >= start)
        .where(ActivityEvent.occurred_at <= end)
        .subquery()
    )
    keys = (inner.c.sub, inner.c.caller, inner.c.ctype, inner.c.oid, inner.c.op, inner.c.res)
    rows = session.execute(
        # A daily row stands for ``event_count`` events (core/activity_compaction.py).
        select(*keys, func.sum(inner.c.n), func.max(inner.c.at)).group_by(*keys)
    ).all()
    return [(*r[:7], as_utc(r[7])) for r in rows]  # type: ignore[misc]


def _caller_correlator(principals: dict[str, dict[str, str | None]]):
    """Same rules as ``role_coverage.correlate_caller`` (JWT object id, then
    exact name, then caller-is-GUID) with an O(1) name index instead of a
    scan over every principal per event."""
    by_name: dict[str, str] = {}
    for pid, info in principals.items():
        name = (info.get("name") or "").lower()
        if name:
            by_name.setdefault(name, pid)

    def correlate(oid: str | None, caller: str | None) -> str | None:
        if oid:
            guid = oid.lower()
            if guid in principals:
                return guid
        if not caller:
            return None
        needle = caller.strip().lower()
        if not needle:
            return None
        if needle in by_name:
            return by_name[needle]
        if needle in principals:
            return needle
        return None

    return correlate


def latest_done_scan_ids(session: Session) -> set[int]:
    """Return the set of scan IDs that are the *latest completed* for their root.

    "Latest" is the most recent ``done`` scan per ``Scan.scope``
    (``created_at`` desc, ``id`` desc as tie-break). Only ``done`` scans
    qualify: a ``failed`` or still-``running`` scan has no (or a partial)
    result set, and letting it win blanked role usage for the whole root
    until the next successful run.
    """
    ranked = (
        select(
            Scan.id.label("id"),
            func.row_number()
            .over(
                partition_by=Scan.scope,
                order_by=(Scan.created_at.desc(), Scan.id.desc()),
            )
            .label("rn"),
        )
        .where(Scan.status == "done")
        .subquery()
    )
    stmt = select(ranked.c.id).where(ranked.c.rn == 1)
    return {row[0] for row in session.execute(stmt).all()}


def build_scan_coverage(session: Session, scan: Scan, window_days: int = 90) -> RoleCoverageResult:
    """Per-RA least-privilege snapshot for one scan.

    For each role assignment in the scan, join with the cached role
    definition (for the granted action patterns) and the activity
    events for the assignment's principal within the window, then
    compute pattern-level coverage. RAs whose role isn't cached
    (older scans pre-migration 0012) are returned with zeroed
    metrics — the UI shows "no data" in that case.

    The window is bounded at 90 days because that's all the Activity
    Log REST API retains.
    """
    # Lazy: tools.iam_access imports app.core.* at module level; this
    # module is imported by the worker at call time, so a top-level
    # import here would be a cycle.
    from app.tools.scope_ids import normalize_scope, role_definition_guid

    scan_id = scan.id
    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(days=window_days)

    ras = list(
        session.scalars(
            select(RoleAssignment)
            .where(RoleAssignment.scan_id == scan_id)
            .order_by(RoleAssignment.principal_id, RoleAssignment.scope)
        ).all()
    )

    # Map principal_id → (name, type) for caller correlation. Includes
    # group MEMBERS in addition to the RA principals themselves —
    # otherwise we can't credit a member's activity (whose caller in
    # Activity Log is the member, never the group) back to the
    # group's coverage row.
    principals: dict[str, dict[str, str | None]] = {}
    for ra in ras:
        pid = ra.principal_id.lower()
        if pid not in principals or (not principals[pid]["name"] and ra.principal_name):
            principals[pid] = {"name": ra.principal_name, "type": ra.principal_type}

    # Group expansion: ``group_id -> [member_id]``. A group present in
    # ``resolved_groups`` with zero members is empty (member_count=0);
    # one absent from it could not be fetched (member_count=None).
    resolved_groups: set[str] = {
        row.group_id.lower()
        for row in session.execute(
            select(ScanGroupResolution).where(ScanGroupResolution.scan_id == scan_id)
        ).scalars()
    }
    members_by_group: dict[str, list[str]] = {}
    member_names: dict[str, str | None] = {}
    member_types: dict[str, str | None] = {}
    for row in session.execute(
        select(GroupMembership).where(GroupMembership.scan_id == scan_id)
    ).scalars():
        gid = row.group_id.lower()
        mid = row.member_id.lower()
        members_by_group.setdefault(gid, []).append(mid)
        member_names.setdefault(mid, row.member_name)
        member_types.setdefault(mid, row.member_type)
    # Legacy scans (pre-0015) have member rows but no resolution row;
    # treat member rows as evidence of a successful resolution.
    resolved_groups |= set(members_by_group.keys())
    for mid, name in member_names.items():
        if mid not in principals:
            principals[mid] = {"name": name, "type": member_types.get(mid)}

    # Subscriptions covered by this scan.
    subs_in_scan = {s for s in (subscription_id_of_scope(ra.scope) for ra in ras) if s}
    root_sub = subscription_id_of_scope(scan.scope)
    if root_sub:
        subs_in_scan.add(root_sub)

    # Events are aggregated in the database: one row per distinct
    # (subscription, caller, object id, operation, resource) with a count
    # and the newest timestamp. A tenant-wide scan can face millions of
    # events in the window; loading them as ORM objects (each carrying a
    # 2–8 KB ``raw`` payload) exhausted the container's memory. Coverage
    # only needs *which* operations a principal performed where, plus
    # counts for the top-operations list, so nothing is lost.
    ops_by_principal: dict[str, Counter[tuple[str, str]]] = {}
    unattributed_counts: Counter[tuple[str, str | None]] = Counter()
    last_activity: dict[str, datetime] = {}
    subs_with_events: set[str] = set()
    events_total = 0
    events_attributed = 0
    # What was done on a management group itself is in the management groups'
    # log; it counts as use of a role given there.
    sources = set(subs_in_scan)
    if any(ra.scope.lower().startswith(MGMT_GROUP_PREFIX) for ra in ras):
        sources.add(MGMT_GROUPS)
    if sources:
        correlate = _caller_correlator(principals)
        for sub, caller, ctype, oid, op, resource, n, newest in _aggregated_events(
            session, sources, window_start, window_end
        ):
            events_total += n
            if sub != MGMT_GROUPS:
                subs_with_events.add(sub)
            pid = correlate(oid, caller)
            if pid is None:
                unattributed_counts[(caller or "(unknown)", ctype)] += n
                continue
            events_attributed += n
            ops_by_principal.setdefault(pid, Counter())[(op, resource or "")] += n
            if newest is not None:
                prev = last_activity.get(pid)
                if prev is None or newest > prev:
                    last_activity[pid] = newest
    top_unattributed = unattributed_counts.most_common(20)

    # Cross-scan lookup for unattributed callers: do we know them from
    # any RA or group membership anywhere?
    needle_set = {caller.lower() for (caller, _), _ in top_unattributed}
    direct_counts: dict[str, int] = {}
    indirect_counts: dict[str, int] = {}
    resolved_names: dict[str, str] = {}
    if needle_set:
        for pid_lower, n in session.execute(
            select(func.lower(RoleAssignment.principal_id), func.count())
            .where(func.lower(RoleAssignment.principal_id).in_(needle_set))
            .group_by(func.lower(RoleAssignment.principal_id))
        ).all():
            direct_counts[pid_lower] = n
        for mid_lower, n in session.execute(
            select(func.lower(GroupMembership.member_id), func.count())
            .where(func.lower(GroupMembership.member_id).in_(needle_set))
            .group_by(func.lower(GroupMembership.member_id))
        ).all():
            indirect_counts[mid_lower] = n
        for pid_lower, name in session.execute(
            select(func.lower(RoleAssignment.principal_id), RoleAssignment.principal_name)
            .where(func.lower(RoleAssignment.principal_id).in_(needle_set))
            .where(RoleAssignment.principal_name.isnot(None))
            .distinct()
        ).all():
            resolved_names.setdefault(pid_lower, name)
        for mid_lower, name in session.execute(
            select(func.lower(GroupMembership.member_id), GroupMembership.member_name)
            .where(func.lower(GroupMembership.member_id).in_(needle_set))
            .where(GroupMembership.member_name.isnot(None))
            .distinct()
        ).all():
            resolved_names.setdefault(mid_lower, name)

    unattributed_callers = [
        UnattributedCaller(
            caller=caller,
            caller_type=ctype,
            event_count=count,
            caller_name=resolved_names.get(caller.lower()),
            direct_assignment_count=direct_counts.get(caller.lower(), 0),
            indirect_membership_count=indirect_counts.get(caller.lower(), 0),
        )
        for (caller, ctype), count in top_unattributed
    ]

    role_defs: dict[str, RoleDefinition] = {
        rd.role_definition_id: rd
        for rd in session.execute(
            select(RoleDefinition).where(
                RoleDefinition.role_definition_id.in_(
                    {role_definition_guid(ra.role_definition_id) for ra in ras}
                )
            )
        ).scalars()
    }

    builtin_candidates = [
        {
            "role_definition_id": rd.role_definition_id,
            "role_name": rd.role_name,
            "role_type": rd.role_type,
            "permissions": rd.permissions,
        }
        for rd in session.execute(
            select(RoleDefinition).where(RoleDefinition.role_type == "BuiltInRole")
        ).scalars()
    ]

    raw_hierarchy: dict[str, dict[str, Any]] = scan.hierarchy or {}
    parent_of: dict[str, str | None] = {}
    for raw_scope, meta in raw_hierarchy.items():
        s = normalize_scope(raw_scope)
        parent = meta.get("parent") if isinstance(meta, dict) else None
        parent_of[s] = normalize_scope(parent) if parent else None

    def ancestors_of(scope: str) -> list[str]:
        chain: list[str] = []
        cur = parent_of.get(scope.lower())
        seen: set[str] = set()
        while cur and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = parent_of.get(cur)
        return chain

    ras_by_principal: dict[str, list[RoleAssignment]] = {}
    for ra in ras:
        ras_by_principal.setdefault(ra.principal_id.lower(), []).append(ra)

    def covered_by_for(ra: RoleAssignment) -> list[CoveredByRef]:
        my_role = role_defs.get(role_definition_guid(ra.role_definition_id))
        if my_role is None:
            return []
        ancestors = set(ancestors_of(ra.scope))
        if not ancestors:
            return []
        out: list[CoveredByRef] = []
        for sibling in ras_by_principal.get(ra.principal_id.lower(), []):
            if sibling.id == ra.id or sibling.scope.lower() not in ancestors:
                continue
            sibling_role = role_defs.get(role_definition_guid(sibling.role_definition_id))
            if sibling_role is None:
                continue
            if not role_covers(sibling_role.permissions, my_role.permissions):
                continue
            out.append(
                CoveredByRef(
                    assignment_id=sibling.id,
                    scope=sibling.scope,
                    scope_name=sibling.scope_name,
                    scope_level=sibling.scope_level,
                    role_name=sibling.role_name,
                )
            )
        return out

    rows: list[RoleCoverageRow] = []
    for ra in ras:
        rd = role_defs.get(role_definition_guid(ra.role_definition_id))
        permissions = rd.permissions if rd else None
        scope_prefix = ra.scope.lower()
        pid_lower = ra.principal_id.lower()
        if ra.principal_type == "Group":
            member_ids = members_by_group.get(pid_lower, [])
            principal_ids = [pid_lower, *member_ids]
            member_count: int | None = len(member_ids) if pid_lower in resolved_groups else None
        else:
            principal_ids = [pid_lower]
            member_count = 0
        principal_ops: Counter[tuple[str, str]] = Counter()
        for principal_id in principal_ids:
            principal_ops.update(ops_by_principal.get(principal_id, {}))
        # Operations observed at or below this RA's scope, with counts.
        relevant: Counter[str] = Counter()
        for (op, resource_id), n in principal_ops.items():
            if not resource_id or resource_id.startswith(scope_prefix):
                relevant[op] += n
        # Distinct operations: pattern matching only cares which ops were seen.
        relevant_ops = list(relevant)
        coverage = compute_coverage(permissions=permissions, observed_operations=relevant_ops)
        # Count desc, then name: deterministic among ties.
        top_ops = [
            TopOperation(operation_name=name, count=count)
            for name, count in sorted(relevant.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        ]
        current_guid = role_definition_guid(ra.role_definition_id)
        recs: list[RoleRecommendation] = []
        if (
            coverage["granted_pattern_count"] > 0
            and coverage["coverage_pct"] < 100
            and relevant_ops
        ):
            candidates = [c for c in builtin_candidates if c["role_definition_id"] != current_guid]
            raw_recs = recommend_narrower_roles(
                observed_operations=relevant_ops,
                current_pattern_count=coverage["granted_pattern_count"],
                candidates=candidates,
            )
            recs = [RoleRecommendation(**r) for r in raw_recs]
        rows.append(
            RoleCoverageRow(
                assignment_id=ra.id,
                scope=ra.scope,
                scope_name=ra.scope_name,
                scope_level=ra.scope_level,
                principal_id=ra.principal_id,
                principal_name=ra.principal_name,
                principal_type=ra.principal_type,
                role_name=ra.role_name,
                role_definition_id=ra.role_definition_id,
                role_type=ra.role_type,
                role_risk=classify_permissions(permissions) if rd else None,
                granted_pattern_count=coverage["granted_pattern_count"],
                used_pattern_count=coverage["used_pattern_count"],
                coverage_pct=coverage["coverage_pct"],
                has_wildcard=coverage["has_wildcard"],
                unused_patterns=coverage["unused_patterns"],
                observed_op_count=len({op for op, _ in principal_ops}),
                recommendations=recs,
                top_operations=top_ops,
                member_count=member_count,
                covered_by=covered_by_for(ra),
            )
        )

    return RoleCoverageResult(
        scan_id=scan.id,
        window_start=window_start,
        window_end=window_end,
        events_considered=events_total,
        events_attributed=events_attributed,
        events_unattributed=events_total - events_attributed,
        subscriptions_without_events=sorted(subs_in_scan - subs_with_events),
        unattributed_callers=unattributed_callers,
        principal_last_activity=last_activity,
        rows=rows,
    )
