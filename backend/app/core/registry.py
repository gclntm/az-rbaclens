"""Scope and principal registries.

Two cross-scan tables keep the *current* picture queryable without
re-deriving it from per-scan rows on every request:

- ``scopes`` — every ARM scope a completed scan has walked, with its
  parent, display name, resource type and ``latest_scan_id``: the most
  recently completed scan whose walk included it. "Newer scan wins" is
  enforced in the upsert itself, so completion order is all that
  matters.
- ``principals`` — every object id seen as an RA principal or group
  member, keeping the newest *non-null* name any scan resolved, plus
  the newest Activity Log timestamp the coverage builder attributed.

Both are updated at the end of ``run_scan`` and can be rebuilt from all
completed scans (``rebuild_registries``) — migration 0018 did exactly
that in SQL for pre-existing data.

Upserts use ``INSERT … ON CONFLICT DO UPDATE``, which both PostgreSQL
and SQLite (tests) support; the dialect is picked from the session's
bind.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, case, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.entra_roles import ENTRA_PLACE
from app.db.models import GroupMembership, Principal, RoleAssignment, RoleEligibility, Scan, Scope

logger = logging.getLogger(__name__)

_CHUNK = 500


def _insert_for(session: Session):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def _chunks(rows: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), _CHUNK):
        yield rows[i : i + _CHUNK]


def _expire_loaded(session: Session, model: type) -> None:
    """Expire only loaded ``model`` instances after a Core UPDATE changed their rows.

    Never ``session.expire_all()`` here: expiring discards *unflushed*
    changes, and callers (e.g. ``compute_scan_findings``) have pending
    updates on other objects in the same session.
    """
    for obj in list(session.identity_map.values()):
        if isinstance(obj, model):
            session.expire(obj)


# --------------------------------------------------------------------------------------
# Scopes
# --------------------------------------------------------------------------------------


def _walked(root: str, parents: dict[str, str], nodes: Iterable[str]) -> set[str]:
    """The root plus every node reachable from it by following child links.

    A scan's hierarchy also contains *ancestors* of the root (MGs above a
    subscription, registered so inherited RAs have somewhere to hang).
    Those were not walked — the scan holds none of their descendants
    outside its own subtree — so they must not claim the scan as their
    latest snapshot.
    """
    children: dict[str, list[str]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    known = set(nodes)
    out = {root}
    # Entra ID sits beside the tree, not under it: a check that read it walked it too.
    if ENTRA_PLACE in known:
        out.add(ENTRA_PLACE)
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child in known and child not in out:
                out.add(child)
                stack.append(child)
    return out


def upsert_scopes_from_scan(session: Session, scan: Scan) -> int:
    """Register every node in ``scan.hierarchy`` (plus the root) for a completed scan.

    Nodes the scan *walked* (root and descendants) are created or, when
    this scan is newer than the row's ``latest_scan_id``, updated and
    claim the pointer. Ancestor-only nodes are registered without a
    pointer and only fill in a missing name. A node the scan was refused
    on (``unread``, a 403) is created if new but never takes the pointer
    from an earlier scan that did read it: its grants there stay current. Parents are linked in a
    final pass so insertion order never trips the self-referencing FK.
    Returns the number of nodes submitted.
    """
    from app.tools.scope_ids import level_from_scope, normalize_scope

    seen_at = scan.finished_at or scan.created_at or datetime.now(UTC)
    hierarchy: dict[str, dict[str, Any]] = scan.hierarchy or {}
    rows: dict[str, dict[str, Any]] = {}
    parents: dict[str, str] = {}
    unread: set[str] = set()
    for raw_scope, meta in hierarchy.items():
        sid = normalize_scope(raw_scope)
        meta = meta if isinstance(meta, dict) else {}
        if meta.get("unread"):
            unread.add(sid)
        rows[sid] = {
            "id": sid,
            "name": meta.get("name"),
            "level": meta.get("level") or level_from_scope(sid),
            "resource_type": meta.get("type"),
            "tags": meta.get("tags") if isinstance(meta.get("tags"), dict) else None,
            "latest_scan_id": scan.id,
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
        }
        parent = meta.get("parent")
        if parent:
            parents[sid] = normalize_scope(parent)
    root = normalize_scope(scan.scope)
    if root not in rows:
        rows[root] = {
            "id": root,
            "name": None,
            "level": scan.scope_level,
            "resource_type": None,
            "tags": None,
            "latest_scan_id": scan.id,
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
        }
    if not rows:
        return 0

    walked = _walked(root, parents, rows.keys())
    walked_rows = [r for sid, r in rows.items() if sid in walked and sid not in unread]
    unread_rows = [r for sid, r in rows.items() if sid in walked and sid in unread]
    ancestor_rows = [{**r, "latest_scan_id": None} for sid, r in rows.items() if sid not in walked]

    insert = _insert_for(session)
    for chunk in _chunks(unread_rows):
        stmt = insert(Scope).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Scope.id],
            set_={"name": func.coalesce(Scope.name, stmt.excluded.name)},
        )
        session.execute(stmt)
    for chunk in _chunks(ancestor_rows):
        stmt = insert(Scope).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Scope.id],
            set_={
                "name": func.coalesce(Scope.name, stmt.excluded.name),
                "resource_type": func.coalesce(Scope.resource_type, stmt.excluded.resource_type),
            },
        )
        session.execute(stmt)
    for chunk in _chunks(walked_rows):
        stmt = insert(Scope).values(chunk)
        newer = or_(
            Scope.latest_scan_id.is_(None), Scope.latest_scan_id < stmt.excluded.latest_scan_id
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Scope.id],
            set_={
                "name": func.coalesce(stmt.excluded.name, Scope.name),
                "level": stmt.excluded.level,
                "resource_type": func.coalesce(stmt.excluded.resource_type, Scope.resource_type),
                # The newest walk is the truth for tags, including "none".
                "tags": stmt.excluded.tags,
                "latest_scan_id": stmt.excluded.latest_scan_id,
                "last_seen_at": stmt.excluded.last_seen_at,
            },
            where=newer,
        )
        session.execute(stmt)

    # Second pass: parent links. Deliberately NOT gated on "newer scan
    # wins": a scan rooted at a subscription knows no parent for its
    # root, while an older MG-rooted scan does. Parent links are
    # structural facts, so any scan that knows one may set it and a scan
    # that doesn't never clears it. Parents outside the walk (an MG above
    # the scan root reached via an inherited RA) are in ``hierarchy``
    # via ensure_ancestor_chain, so the FK is satisfiable.
    link_rows = [{"b_id": sid, "b_parent": pid} for sid, pid in parents.items() if pid in rows]
    if link_rows:
        # Core table, not the mapped class: an ORM ``update()`` given a
        # parameter list switches to "bulk UPDATE by primary key" mode and
        # rejects bindparam-driven WHERE clauses.
        session.execute(
            update(Scope.__table__)
            .where(Scope.__table__.c.id == bindparam("b_id"))
            .values(parent_id=bindparam("b_parent")),
            link_rows,
        )
        _expire_loaded(session, Scope)
    return len(rows)


# --------------------------------------------------------------------------------------
# Principals
# --------------------------------------------------------------------------------------


def upsert_principals_from_scan(session: Session, scan: Scan) -> int:
    """Register every principal referenced by the scan's RAs, PIM
    eligibilities and group members."""
    seen_at = scan.finished_at or scan.created_at or datetime.now(UTC)
    refs: dict[str, dict[str, Any]] = {}

    def add(
        pid: str | None,
        name: str | None,
        ptype: str | None,
        owner: str | None = None,
        kind: str | None = None,
    ) -> None:
        if not pid:
            return
        key = pid.strip().lower()
        if not key:
            return
        cur = refs.get(key)
        if cur is None:
            refs[key] = {
                "id": key,
                "display_name": name,
                "principal_type": ptype,
                "app_owner_org": owner,
                "sp_type": kind,
            }
        else:
            for field, value in (
                ("display_name", name),
                ("principal_type", ptype),
                ("app_owner_org", owner),
                ("sp_type", kind),
            ):
                if value and not cur[field]:
                    cur[field] = value

    for pid, name, ptype, owner, kind in session.execute(
        select(
            RoleAssignment.principal_id,
            RoleAssignment.principal_name,
            RoleAssignment.principal_type,
            RoleAssignment.principal_app_owner,
            RoleAssignment.principal_sp_type,
        ).where(RoleAssignment.scan_id == scan.id)
    ).all():
        add(pid, name, ptype, owner, kind)
    for pid, name, ptype in session.execute(
        select(
            RoleEligibility.principal_id,
            RoleEligibility.principal_name,
            RoleEligibility.principal_type,
        ).where(RoleEligibility.scan_id == scan.id)
    ).all():
        add(pid, name, ptype)
    for mid, name, mtype in session.execute(
        select(
            GroupMembership.member_id, GroupMembership.member_name, GroupMembership.member_type
        ).where(GroupMembership.scan_id == scan.id)
    ).all():
        add(mid, name, mtype)
    if not refs:
        return 0

    rows = [
        {
            **r,
            "resolved_at": seen_at if r["display_name"] else None,
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
        }
        for r in refs.values()
    ]
    insert = _insert_for(session)
    for chunk in _chunks(rows):
        stmt = insert(Principal).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Principal.id],
            set_={
                "display_name": func.coalesce(stmt.excluded.display_name, Principal.display_name),
                "principal_type": func.coalesce(
                    stmt.excluded.principal_type, Principal.principal_type
                ),
                "app_owner_org": func.coalesce(
                    stmt.excluded.app_owner_org, Principal.app_owner_org
                ),
                "sp_type": func.coalesce(stmt.excluded.sp_type, Principal.sp_type),
                "resolved_at": case(
                    (stmt.excluded.display_name.isnot(None), stmt.excluded.resolved_at),
                    else_=Principal.resolved_at,
                ),
                "last_seen_at": func.coalesce(
                    func.max(stmt.excluded.last_seen_at, Principal.last_seen_at)
                    if session.get_bind().dialect.name != "postgresql"
                    else func.greatest(stmt.excluded.last_seen_at, Principal.last_seen_at),
                    stmt.excluded.last_seen_at,
                ),
            },
        )
        session.execute(stmt)
    return len(rows)


def record_principal_activity(session: Session, last_activity: dict[str, datetime]) -> int:
    """Advance ``principals.last_activity_at`` for the given ids (never moves it back)."""
    rows = [
        {"b_id": pid.lower(), "b_at": at}
        for pid, at in last_activity.items()
        if pid and at is not None
    ]
    if not rows:
        return 0
    t = Principal.__table__
    result = session.execute(
        update(t)
        .where(t.c.id == bindparam("b_id"))
        .where(or_(t.c.last_activity_at.is_(None), t.c.last_activity_at < bindparam("b_at")))
        .values(last_activity_at=bindparam("b_at")),
        rows,
    )
    # Loaded Principal instances (if any) now hold stale last_activity_at.
    _expire_loaded(session, Principal)
    return result.rowcount or 0


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------


def update_registries_for_scan(session: Session, scan: Scan) -> tuple[int, int]:
    """Scopes + principals for one completed scan. Does not commit."""
    if scan.status != "done":
        return 0, 0
    scopes = upsert_scopes_from_scan(session, scan)
    principals = upsert_principals_from_scan(session, scan)
    return scopes, principals


def rebuild_registries(session: Session) -> dict[str, int]:
    """Re-derive both registries from every completed scan, oldest first.

    Idempotent. Scope pointers are cleared first and replayed, so a
    pointer claimed under older rules (or by a since-deleted scan) cannot
    block the correct owner via the "newer wins" guard. Rows for scopes
    or principals that no longer appear in any completed scan are left in
    place (history, not truth) — delete them manually if ever needed.
    Commits.
    """
    session.execute(update(Scope.__table__).values(latest_scan_id=None))
    scan_ids = list(
        session.scalars(
            select(Scan.id).where(Scan.status == "done").order_by(Scan.created_at, Scan.id)
        ).all()
    )
    scopes = principals = 0
    for scan_id in scan_ids:
        scan = session.get(Scan, scan_id)
        if scan is None:
            continue
        s, p = update_registries_for_scan(session, scan)
        scopes += s
        principals += p
    session.commit()
    logger.info(
        "registries rebuilt from %d scans: %d scope rows, %d principal rows submitted",
        len(scan_ids),
        scopes,
        principals,
    )
    return {"scans": len(scan_ids), "scopes": scopes, "principals": principals}
