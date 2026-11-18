"""Owners added to or removed from the apps and groups that hold access (plan "owners").

An app's owner can add a secret to it and act as it; a group's owner decides
its members. A new owner is a change on the Changes page, like someone
joining a group: ``owner_added`` / ``owner_removed``, with the owner as the
identity and the app or group in ``via_group_*``; the place, role and level
are the strongest grant the app or group holds.

Two sources, as for Entra roles (``core/changes.py``):

- comparing two checks of a place (:func:`owner_changes`), for apps and
  groups whose owners both checks read. Author and exact time come from the
  Entra ID audit log when it holds the entry (:func:`apply_owner_audits`);
- straight from the audit log (:func:`record_owner_audit_changes`): an owner
  added and removed between two checks, or before the first check that read
  owners. Only for apps and groups that hold access now.

An app's registration appears in the audit log by its client id
(``appid:<id>``, ``tools/entra_audit.py``); the checks store each app's
client id (``scan_owner_resolutions.app_id``) to match it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.capabilities import LEVEL_RANK, UNKNOWN, classify
from app.core.timeutil import as_utc
from app.db.models import (
    AccessChange,
    DirectoryAuditEvent,
    PrincipalOwner,
    RoleAssignment,
    RoleDefinition,
    Scan,
    ScanOwnerResolution,
)
from app.tools.entra_audit import APP_PREFIX

OWNER_KINDS = ("owner_added", "owner_removed")
_ACTION = {"owner_added": "add", "owner_removed": "remove"}
_KIND = {"add": "owner_added", "remove": "owner_removed"}
# Entries this close are one action (the registration and the service principal at once).
_SAME_ACTION = timedelta(minutes=10)
# A check sees a change at the latest at the next daily check (plus a margin).
_SEEN_BY_A_CHECK = timedelta(hours=26)
_CHUNK = 500

Owned = dict[str, dict[str, tuple[str | None, str | None]]]


def _chunks(items: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(items), _CHUNK):
        yield items[i : i + _CHUNK]


def subject_key(object_id: str, owner_id: str) -> str:
    return f"owner:{object_id.lower()}:{owner_id.lower()}"


def _owners(session: Session, scan_id: int) -> Owned:
    """``{app or group: {owner: (name, type)}}`` for those whose owners this check read."""
    out: Owned = {
        o.lower(): {}
        for (o,) in session.execute(
            select(ScanOwnerResolution.object_id).where(ScanOwnerResolution.scan_id == scan_id)
        ).all()
    }
    for oid, owner, name, otype in session.execute(
        select(
            PrincipalOwner.object_id,
            PrincipalOwner.owner_id,
            PrincipalOwner.owner_name,
            PrincipalOwner.owner_type,
        ).where(PrincipalOwner.scan_id == scan_id)
    ).all():
        out.setdefault(oid.lower(), {})[owner.lower()] = (name, otype)
    return out


def _strongest(grants: list, level_of) -> object:
    return min(grants, key=lambda g: LEVEL_RANK[level_of(g.role_definition_id)])


def owner_changes(
    session: Session,
    prev: Scan,
    scan: Scan,
    old: dict,
    new: dict,
    level_of,
    seen_at: datetime,
) -> list[AccessChange]:
    """Owners added or removed between two checks, for apps and groups holding access.

    ``old`` / ``new``: each check's grants (``core/changes._Grant`` by key);
    ``level_of``: role definition id → level.
    """
    before, after = _owners(session, prev.id), _owners(session, scan.id)
    out: list[AccessChange] = []
    for kind, now, then, grants in (
        ("owner_added", after, before, new),
        ("owner_removed", before, after, old),
    ):
        held: dict[str, list] = defaultdict(list)
        for g in grants.values():
            held[g.principal_id.lower()].append(g)
        for oid in now.keys() & then.keys():
            if not held.get(oid):
                continue
            g = _strongest(held[oid], level_of)
            for owner in now[oid].keys() - then[oid].keys():
                name, otype = now[oid][owner]
                out.append(
                    AccessChange(
                        kind=kind,
                        occurred_at=seen_at,
                        source="daily_check",
                        first_seen_scan_id=scan.id,
                        scope=g.scope,
                        scope_name=g.scope_name,
                        scope_level=g.scope_level,
                        principal_id=owner,
                        principal_name=name,
                        principal_type=otype,
                        role_definition_id=g.role_definition_id,
                        role_name=g.role_name,
                        level=level_of(g.role_definition_id),
                        via_group_id=oid,
                        via_group_name=g.principal_name,
                        subject_key=subject_key(oid, owner),
                    )
                )
    return out


def _app_ids(session: Session, object_ids: set[str]) -> dict[str, str]:
    """``{service principal: client id}`` from the checks that read their owners."""
    out: dict[str, str] = {}
    for chunk in _chunks(sorted(object_ids)):
        for oid, app_id in session.execute(
            select(ScanOwnerResolution.object_id, ScanOwnerResolution.app_id)
            .where(ScanOwnerResolution.object_id.in_(chunk))
            .where(ScanOwnerResolution.app_id.is_not(None))
            .order_by(ScanOwnerResolution.scan_id)
        ).all():
            out[oid.lower()] = str(app_id).lower()
    return out


def _events(
    session: Session, owner_ids: set[str], since: datetime | None, until: datetime | None = None
) -> list[DirectoryAuditEvent]:
    q = select(DirectoryAuditEvent).where(DirectoryAuditEvent.kind == "owner")
    if owner_ids:
        q = q.where(DirectoryAuditEvent.member_id.in_(sorted(owner_ids)))
    if since is not None:
        q = q.where(DirectoryAuditEvent.occurred_at >= since)
    if until is not None:
        q = q.where(DirectoryAuditEvent.occurred_at <= until)
    return [
        e
        for e in session.scalars(
            q.order_by(DirectoryAuditEvent.occurred_at, DirectoryAuditEvent.id)
        ).all()
        if (e.result or "success").lower() == "success" and e.group_id
    ]


def apply_owner_audits(
    session: Session, changes: list[AccessChange], since: datetime, until: datetime
) -> int:
    """Give owner changes the time and author of their Entra ID audit entry: same
    owner, same app or group (its registration or its service principal), same
    action, between the two checks; the newest wins. The caller commits."""
    wanted = [c for c in changes if c.kind in _ACTION and c.via_group_id]
    if not wanted:
        return 0
    objects = {(c.via_group_id or "").lower() for c in wanted}
    app_ids = _app_ids(session, objects)
    events: dict[tuple[str, str, str], list[DirectoryAuditEvent]] = defaultdict(list)
    for chunk in _chunks(sorted({c.principal_id.lower() for c in wanted})):
        for e in _events(session, set(chunk), since, until):
            events[(e.member_id, e.group_id or "", e.action)].append(e)
    updated = 0
    for c in wanted:
        oid, owner, action = (c.via_group_id or "").lower(), c.principal_id.lower(), _ACTION[c.kind]
        keys = [oid] + ([f"{APP_PREFIX}{app_ids[oid]}"] if oid in app_ids else [])
        hits = [e for k in keys for e in events.get((owner, k, action), [])]
        if not hits:
            continue
        ev = max(hits, key=lambda e: (as_utc(e.occurred_at), e.id or 0))
        c.occurred_at = as_utc(ev.occurred_at)
        c.source = "entra_audit"
        c.actor_id = ev.initiator_id
        c.actor_name = ev.initiator_name
        c.actor_type = ev.initiator_type
        updated += 1
    return updated


def _current_grants(session: Session, object_ids: set[str]) -> dict[str, list[RoleAssignment]]:
    """Each object's grants in the newest completed check that has any of them."""
    out: dict[str, list[RoleAssignment]] = {}
    for chunk in _chunks(sorted(object_ids)):
        newest = (
            select(
                func.lower(RoleAssignment.principal_id).label("pid"),
                func.max(RoleAssignment.scan_id).label("scan_id"),
            )
            .join(Scan, Scan.id == RoleAssignment.scan_id)
            .where(Scan.status == "done")
            .where(func.lower(RoleAssignment.principal_id).in_(chunk))
            .group_by(func.lower(RoleAssignment.principal_id))
            .subquery()
        )
        for ra in session.scalars(
            select(RoleAssignment).join(
                newest,
                (newest.c.pid == func.lower(RoleAssignment.principal_id))
                & (newest.c.scan_id == RoleAssignment.scan_id),
            )
        ).all():
            out.setdefault(ra.principal_id.lower(), []).append(ra)
    return out


def record_owner_audit_changes(session: Session, since: datetime | None = None) -> int:
    """Add the owner changes the audit log holds that the feed doesn't have yet. Commits.

    Each owner added or removed becomes a change with its author and exact
    time, for apps and groups that hold access now. Entries for the same
    owner, object and action within minutes are one (an app's registration and
    service principal at once). A change already in the feed (at its audit
    time, or first seen by a later check) is not added again.
    """
    rows = _events(session, set(), since)
    if not rows:
        return 0
    sp_of = {
        f"{APP_PREFIX}{app_id}": oid
        for oid, app_id in _app_ids(
            session,
            {
                o.lower()
                for (o,) in session.execute(
                    select(ScanOwnerResolution.object_id)
                    .where(ScanOwnerResolution.app_id.is_not(None))
                    .distinct()
                ).all()
            },
        ).items()
    }
    entries: dict[tuple[str, str, str], list[DirectoryAuditEvent]] = defaultdict(list)
    for e in rows:
        oid = sp_of.get(e.group_id or "", e.group_id or "")
        if not oid.startswith(APP_PREFIX):
            entries[(e.member_id, oid, e.action)].append(e)
    grants = _current_grants(session, {oid for _m, oid, _a in entries})
    if not grants:
        return 0
    guids = {
        ra.role_definition_id.rsplit("/", 1)[-1].lower() for gs in grants.values() for ra in gs
    }
    levels = {
        rid.rsplit("/", 1)[-1].lower(): classify(perms).level
        for rid, perms in session.execute(
            select(RoleDefinition.role_definition_id, RoleDefinition.permissions)
        ).all()
        if rid.rsplit("/", 1)[-1].lower() in guids
    }

    def level_of(role_definition_id: str) -> str:
        return levels.get(role_definition_id.rsplit("/", 1)[-1].lower(), UNKNOWN.level)

    keys = sorted({subject_key(oid, m) for (m, oid, _a) in entries if oid in grants})
    existing: dict[tuple[str, str], list[tuple[datetime, str]]] = defaultdict(list)
    for chunk in _chunks(keys):
        for key, kind, at, source in session.execute(
            select(
                AccessChange.subject_key,
                AccessChange.kind,
                AccessChange.occurred_at,
                AccessChange.source,
            ).where(AccessChange.subject_key.in_(chunk))
        ).all():
            existing[(key, kind)].append((as_utc(at), source))

    added = 0
    for (owner, oid, action), group in entries.items():
        if oid not in grants:
            continue
        picked: list[DirectoryAuditEvent] = []
        for e in group:
            if not picked or as_utc(e.occurred_at) - as_utc(picked[-1].occurred_at) > _SAME_ACTION:
                picked.append(e)
        g = _strongest(grants[oid], level_of)
        kind, key = _KIND[action], subject_key(oid, owner)
        for e in picked:
            at = as_utc(e.occurred_at)
            if any(
                abs(when - at) <= _SAME_ACTION
                or (source == "daily_check" and at <= when <= at + _SEEN_BY_A_CHECK)
                for when, source in existing.get((key, kind), [])
            ):
                continue
            session.add(
                AccessChange(
                    kind=kind,
                    occurred_at=at,
                    source="entra_audit",
                    scope=g.scope,
                    scope_name=g.scope_name,
                    scope_level=g.scope_level,
                    principal_id=owner,
                    principal_name=e.member_name,
                    principal_type=e.member_type,
                    role_definition_id=g.role_definition_id,
                    role_name=g.role_name,
                    level=level_of(g.role_definition_id),
                    via_group_id=oid,
                    via_group_name=g.principal_name or e.group_name,
                    actor_id=e.initiator_id,
                    actor_name=e.initiator_name,
                    actor_type=e.initiator_type,
                    subject_key=key,
                )
            )
            existing[(key, kind)].append((at, "entra_audit"))
            added += 1
    session.commit()
    return added
