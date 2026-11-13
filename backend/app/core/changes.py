"""The changes feed: who got or lost access, when, and who did it.

Each completed check is compared with the previous completed check of the
same place (``previous_done_scan``), so both walked the same subtree.
Three kinds of difference become ``access_changes`` rows:

- grants given or removed (role assignments);
- PIM eligibilities added or removed (only when both checks read PIM);
- people joining or leaving a group that holds access (only for groups
  both checks could read).

Time and author come from the best source available, in this order: the
matching Activity Log event (by Azure's assignment id), Azure's own
``createdOn`` / ``createdBy`` on a new grant, or the check itself ("first
seen", no author). A later Activity Log pull can still upgrade a "first
seen" entry (``refine_from_events``).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.activity_targets import MGMT_GROUP_PREFIX, is_mgmt_groups
from app.core.capabilities import LEVEL_RANK, UNKNOWN, classify
from app.core.findings import grant_fingerprint, previous_done_scan
from app.core.timeutil import as_utc
from app.db.models import (
    AccessChange,
    ActivityEvent,
    GroupMembership,
    Principal,
    RoleAssignment,
    RoleDefinition,
    RoleEligibility,
    Scan,
    ScanGroupResolution,
)

logger = logging.getLogger(__name__)

GIVEN_KINDS = ("granted", "eligible_added", "member_added")
REMOVED_KINDS = ("removed", "eligible_removed", "member_removed")
WRITE_OP = "microsoft.authorization/roleassignments/write"
DELETE_OP = "microsoft.authorization/roleassignments/delete"
# How far before the previous check an event may lie and still explain a
# difference (events are stamped when the request started).
EVENT_SLACK = timedelta(hours=6)
_PIM_READ = ("read", "partial")
_CHUNK = 500


@dataclass
class _Grant:
    scope: str
    scope_name: str | None
    scope_level: str
    principal_id: str
    principal_name: str | None
    principal_type: str | None
    role_definition_id: str
    role_name: str | None
    azure_id: str | None
    created_on: datetime | None = None
    created_by: str | None = None
    created_by_name: str | None = None
    start_at: datetime | None = None

    @property
    def key(self) -> str:
        return grant_fingerprint(self.scope, self.role_definition_id, self.principal_id)


def _chunks(items: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


# ---- loading ---------------------------------------------------------------------------


def _grants(session: Session, scan_id: int) -> dict[str, _Grant]:
    rows = session.execute(
        select(
            RoleAssignment.scope,
            RoleAssignment.scope_name,
            RoleAssignment.scope_level,
            RoleAssignment.principal_id,
            RoleAssignment.principal_name,
            RoleAssignment.principal_type,
            RoleAssignment.role_definition_id,
            RoleAssignment.role_name,
            RoleAssignment.raw["id"].as_string(),
            RoleAssignment.created_on,
            RoleAssignment.created_by,
            RoleAssignment.created_by_name,
        ).where(RoleAssignment.scan_id == scan_id)
    ).all()
    out: dict[str, _Grant] = {}
    for r in rows:
        g = _Grant(
            r[0].lower(),
            r[1],
            r[2],
            r[3].lower(),
            r[4],
            r[5],
            r[6],
            r[7],
            (r[8] or "").lower() or None,
            as_utc(r[9]),
            r[10],
            r[11],
        )
        out[g.key] = g
    return out


def _eligibilities(session: Session, scan_id: int) -> dict[str, _Grant]:
    rows = session.scalars(select(RoleEligibility).where(RoleEligibility.scan_id == scan_id)).all()
    out: dict[str, _Grant] = {}
    for e in rows:
        g = _Grant(
            e.scope.lower(),
            e.scope_name,
            e.scope_level,
            e.principal_id.lower(),
            e.principal_name,
            e.principal_type,
            e.role_definition_id,
            e.role_name,
            e.azure_id.lower(),
            start_at=as_utc(e.start_at),
        )
        out[g.key] = g
    return out


def _members(session: Session, scan_id: int) -> dict[str, dict[str, tuple[str | None, str | None]]]:
    """``{group: {member: (name, type)}}`` for groups this check could read."""
    resolved = {
        g.lower()
        for (g,) in session.execute(
            select(ScanGroupResolution.group_id).where(ScanGroupResolution.scan_id == scan_id)
        ).all()
    }
    out: dict[str, dict[str, tuple[str | None, str | None]]] = {g: {} for g in resolved}
    for gid, mid, name, mtype in session.execute(
        select(
            GroupMembership.group_id,
            GroupMembership.member_id,
            GroupMembership.member_name,
            GroupMembership.member_type,
        ).where(GroupMembership.scan_id == scan_id)
    ).all():
        out.setdefault(gid.lower(), {})[mid.lower()] = (name, mtype)
    return out


def _levels(session: Session, role_ids: set[str]) -> dict[str, str]:
    """``{role GUID: level}``."""
    guids = {r.rsplit("/", 1)[-1].lower() for r in role_ids}
    out = dict.fromkeys(guids, UNKNOWN.level)
    for chunk in _chunks(sorted(guids)):
        for guid, permissions in session.execute(
            select(RoleDefinition.role_definition_id, RoleDefinition.permissions).where(
                func.lower(RoleDefinition.role_definition_id).in_(chunk)
            )
        ).all():
            out[guid.lower()] = classify(permissions).level
    return out


def _level_of(levels: dict[str, str], role_definition_id: str) -> str:
    return levels.get(role_definition_id.rsplit("/", 1)[-1].lower(), UNKNOWN.level)


def _events(
    session: Session, azure_ids: set[str], since: datetime
) -> dict[tuple[str, str], ActivityEvent]:
    """Newest succeeded write / delete event per ``(assignment id, op)`` since ``since``."""
    out: dict[tuple[str, str], ActivityEvent] = {}
    ids = sorted(i for i in azure_ids if i)
    for chunk in _chunks(ids):
        for ev in session.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.occurred_at >= since)
            .where(func.lower(ActivityEvent.operation_name).in_((WRITE_OP, DELETE_OP)))
            .where(ActivityEvent.status == "Succeeded")
            .where(func.lower(ActivityEvent.resource_id).in_(chunk))
        ).all():
            key = ((ev.resource_id or "").lower(), ev.operation_name.lower())
            if key not in out or as_utc(ev.occurred_at) > as_utc(out[key].occurred_at):
                out[key] = ev
    return out


def _names(session: Session, ids: set[str]) -> dict[str, str]:
    ids = {i for i in ids if i}
    out: dict[str, str] = {}
    for chunk in _chunks(sorted(ids)):
        out.update(
            {
                pid: name
                for pid, name in session.execute(
                    select(Principal.id, Principal.display_name).where(Principal.id.in_(chunk))
                ).all()
                if name
            }
        )
    return out


# ---- building -------------------------------------------------------------------------


def _change(kind: str, g: _Grant, level: str, scan: Scan, **extra: Any) -> AccessChange:
    return AccessChange(
        kind=kind,
        scope=g.scope,
        scope_name=g.scope_name,
        scope_level=g.scope_level,
        principal_id=g.principal_id,
        principal_name=g.principal_name,
        principal_type=g.principal_type,
        role_definition_id=g.role_definition_id,
        role_name=g.role_name,
        level=level,
        azure_id=g.azure_id,
        first_seen_scan_id=scan.id,
        **extra,
    )


def changes_between(session: Session, prev: Scan, scan: Scan) -> list[AccessChange]:
    """The changes that explain the difference between two checks of one place."""
    seen_at = as_utc(scan.finished_at) or datetime.now(UTC)
    prev_at = as_utc(prev.finished_at) or seen_at
    old, new = _grants(session, prev.id), _grants(session, scan.id)
    added = [new[k] for k in new.keys() - old.keys()]
    removed = [old[k] for k in old.keys() - new.keys()]
    # A place this check was refused on (403) is unknown, not empty: grants
    # the previous check saw at or under it are not "removed".
    unread = [
        s for s, n in (scan.hierarchy or {}).items() if isinstance(n, dict) and n.get("unread")
    ]
    if unread:
        removed = [
            g
            for g in removed
            if not any(g.scope == u or g.scope.startswith(u + "/") for u in unread)
        ]

    pim = prev.eligibility_status in _PIM_READ and scan.eligibility_status in _PIM_READ
    old_e = _eligibilities(session, prev.id) if pim else {}
    new_e = _eligibilities(session, scan.id) if pim else {}
    e_added = [new_e[k] for k in new_e.keys() - old_e.keys()]
    e_removed = [old_e[k] for k in old_e.keys() - new_e.keys()]

    levels = _levels(
        session,
        {g.role_definition_id for g in [*added, *removed, *e_added, *e_removed]}
        | {g.role_definition_id for g in [*old.values(), *new.values()]},
    )
    events = _events(
        session, {g.azure_id for g in [*added, *removed] if g.azure_id}, prev_at - EVENT_SLACK
    )

    out: list[AccessChange] = []
    for g in added:
        ev = events.get((g.azure_id or "", WRITE_OP))
        if ev is not None:
            when, actor, source = as_utc(ev.occurred_at), ev.caller_oid, "activity_log"
        elif g.created_on and g.created_on >= prev_at - EVENT_SLACK:
            when, actor, source = g.created_on, g.created_by, "created_on"
        else:
            when, actor, source = seen_at, None, "daily_check"
        out.append(
            _change(
                "granted",
                g,
                _level_of(levels, g.role_definition_id),
                scan,
                occurred_at=when,
                actor_id=actor,
                source=source,
                actor_name=g.created_by_name if source == "created_on" else None,
                activity_event_id=ev.id if ev is not None else None,
                subject_key=g.key,
            )
        )
    for g in removed:
        ev = events.get((g.azure_id or "", DELETE_OP))
        out.append(
            _change(
                "removed",
                g,
                _level_of(levels, g.role_definition_id),
                scan,
                occurred_at=as_utc(ev.occurred_at) if ev is not None else seen_at,
                actor_id=ev.caller_oid if ev is not None else None,
                source="activity_log" if ev is not None else "daily_check",
                activity_event_id=ev.id if ev is not None else None,
                subject_key=g.key,
            )
        )
    for g in e_added:
        exact = g.start_at is not None and g.start_at >= prev_at - EVENT_SLACK
        out.append(
            _change(
                "eligible_added",
                g,
                _level_of(levels, g.role_definition_id),
                scan,
                occurred_at=g.start_at if exact else seen_at,
                source="created_on" if exact else "daily_check",
                subject_key=g.key,
            )
        )
    for g in e_removed:
        out.append(
            _change(
                "eligible_removed",
                g,
                _level_of(levels, g.role_definition_id),
                scan,
                occurred_at=seen_at,
                source="daily_check",
                subject_key=g.key,
            )
        )

    out.extend(_membership_changes(session, prev, scan, old, new, levels, seen_at))

    names = _names(session, {c.actor_id for c in out if c.actor_id and not c.actor_name})
    for c in out:
        if c.actor_id and not c.actor_name:
            c.actor_name = names.get(c.actor_id.lower())
    return out


def _membership_changes(
    session: Session,
    prev: Scan,
    scan: Scan,
    old: dict[str, _Grant],
    new: dict[str, _Grant],
    levels: dict[str, str],
    seen_at: datetime,
) -> list[AccessChange]:
    old_m, new_m = _members(session, prev.id), _members(session, scan.id)
    out: list[AccessChange] = []
    for kind, members_now, members_before, grants in (
        ("member_added", new_m, old_m, new),
        ("member_removed", old_m, new_m, old),
    ):
        by_group: dict[str, list[_Grant]] = defaultdict(list)
        for g in grants.values():
            by_group[g.principal_id].append(g)
        for gid in members_now.keys() & members_before.keys():
            held = by_group.get(gid)
            if not held:
                continue
            strongest = min(held, key=lambda g: LEVEL_RANK[_level_of(levels, g.role_definition_id)])
            for mid in members_now[gid].keys() - members_before[gid].keys():
                name, mtype = members_now[gid][mid]
                out.append(
                    AccessChange(
                        kind=kind,
                        occurred_at=seen_at,
                        source="daily_check",
                        first_seen_scan_id=scan.id,
                        scope=strongest.scope,
                        scope_name=strongest.scope_name,
                        scope_level=strongest.scope_level,
                        principal_id=mid,
                        principal_name=name,
                        principal_type=mtype,
                        role_definition_id=strongest.role_definition_id,
                        role_name=strongest.role_name,
                        level=_level_of(levels, strongest.role_definition_id),
                        via_group_id=gid,
                        via_group_name=strongest.principal_name,
                        subject_key=f"member:{gid}:{mid}",
                    )
                )
    return out


def record_changes_for_scan(session: Session, scan_id: int) -> int:
    """Write the changes a just-completed check found; returns how many. Commits.

    Skips entries already recorded since the previous check (the same
    change seen by an overlapping check of another place).
    """
    scan = session.get(Scan, scan_id)
    if scan is None or scan.status != "done":
        return 0
    prev = previous_done_scan(session, scan)
    if prev is None:
        return 0
    found = changes_between(session, prev, scan)
    since = (as_utc(prev.finished_at) or datetime.now(UTC)) - EVENT_SLACK
    keys = sorted({c.subject_key for c in found})
    existing: set[tuple[str, str]] = set()
    for chunk in _chunks(keys):
        existing.update(
            session.execute(
                select(AccessChange.subject_key, AccessChange.kind)
                .where(AccessChange.subject_key.in_(chunk))
                .where(AccessChange.occurred_at >= since)
            ).all()
        )
    fresh = [c for c in found if (c.subject_key, c.kind) not in existing]
    session.add_all(fresh)
    session.commit()
    return len(fresh)


def rebuild_changes(session: Session) -> int:
    """Recompute the whole feed from the checks on file, oldest first. Commits."""
    session.execute(delete(AccessChange))
    session.commit()
    total = 0
    for (scan_id,) in session.execute(
        select(Scan.id).where(Scan.status == "done").order_by(Scan.finished_at, Scan.id)
    ).all():
        total += record_changes_for_scan(session, scan_id)
    return total


def refine_from_events(session: Session, subscription_id: str, days: int = 14) -> int:
    """Give "first seen" grant changes the exact time and author once their
    Activity Log events arrive (pulls lag behind checks). ``subscription_id`` may be
    ``management-groups``: then the changes on management groups. Commits."""
    since = datetime.now(UTC) - timedelta(days=days)
    prefix = (
        MGMT_GROUP_PREFIX
        if is_mgmt_groups(subscription_id)
        else f"/subscriptions/{subscription_id.lower()}"
    )
    rows = session.scalars(
        select(AccessChange)
        .where(AccessChange.source == "daily_check")
        .where(AccessChange.kind.in_(("granted", "removed")))
        .where(AccessChange.azure_id.is_not(None))
        .where(AccessChange.occurred_at >= since)
        .where(func.lower(AccessChange.scope).like(f"{prefix}%"))
    ).all()
    if not rows:
        return 0
    events = _events(session, {r.azure_id for r in rows if r.azure_id}, since - EVENT_SLACK)
    names = _names(session, {ev.caller_oid for ev in events.values() if ev.caller_oid})
    updated = 0
    for r in rows:
        ev = events.get((r.azure_id or "", WRITE_OP if r.kind == "granted" else DELETE_OP))
        if ev is None:
            continue
        r.occurred_at = as_utc(ev.occurred_at)
        r.actor_id = ev.caller_oid
        r.actor_name = names.get((ev.caller_oid or "").lower())
        r.source = "activity_log"
        r.activity_event_id = ev.id
        updated += 1
    try:
        session.commit()
    except IntegrityError:
        # The exact entry already exists (recorded by an overlapping check).
        session.rollback()
        return 0
    return updated
