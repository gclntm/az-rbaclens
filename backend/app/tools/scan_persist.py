"""Saving what a check read: role definitions, and the members of each group."""

from __future__ import annotations

import logging
from typing import Any

from app.db.models import (
    GroupMembership,
    RoleDefinition,
    ScanGroupResolution,
)

logger = logging.getLogger(__name__)


def persist_group_memberships(
    session: Any,
    scan_id: int,
    memberships: dict[str, list[Any] | None],
) -> None:
    """Persist group-resolution status + per-member rows for the scan.

    The map's value distinguishes two outcomes:

    - ``list[GroupMember]`` (possibly empty) — Graph returned. We
      write a ``ScanGroupResolution`` row to record "resolved" *and*
      one ``GroupMembership`` per member (zero rows when empty).
    - ``None`` — Graph refused / errored. We write NEITHER, so the
      Coverage tab can render "membership unresolved" by checking
      the presence of a resolution row.

    Skips deduping in-Python because the scan that just ran owns
    these rows exclusively (CASCADE-on-delete from ``scans``).
    """
    if not memberships:
        return
    for group_id, members in memberships.items():
        if members is None:
            # Graph failure — leave no resolution row so the
            # Coverage endpoint treats this as "unresolved".
            continue
        session.add(ScanGroupResolution(scan_id=scan_id, group_id=group_id))
        for m in members:
            if not m.id:
                continue
            session.add(
                GroupMembership(
                    scan_id=scan_id,
                    group_id=group_id,
                    member_id=m.id,
                    member_type=m.type,
                    member_name=m.name,
                )
            )


def upsert_role_definitions(session: Any, role_definitions: dict[str, dict[str, Any]]) -> None:
    """Upsert every distinct role definition seen during the scan.

    Operates row-by-row via ``session.get``/``merge``-style logic so the
    same code path works for both Postgres (where we could use
    ``insert(...).on_conflict_do_update``) and the in-memory SQLite the
    tests use. The volume is tiny — a few dozen distinct roles per scan
    — so per-row trips don't matter.
    """
    for guid, info in role_definitions.items():
        existing = session.get(RoleDefinition, guid)
        if existing is None:
            session.add(
                RoleDefinition(
                    role_definition_id=guid,
                    role_name=info.get("name"),
                    role_type=info.get("role_type"),
                    description=info.get("description"),
                    permissions=info.get("permissions"),
                    assignable_scopes=info.get("assignable_scopes"),
                )
            )
        else:
            existing.role_name = info.get("name")
            existing.role_type = info.get("role_type")
            existing.description = info.get("description")
            existing.permissions = info.get("permissions")
            existing.assignable_scopes = info.get("assignable_scopes")
