"""Who gave a grant and when, from what Azure records on the assignment.

Azure keeps ``createdOn`` / ``createdBy`` (object id) / ``updatedOn`` on
every role assignment. The creator is usually someone who also holds a
role (named by the snapshot's own principal resolution) or a pipeline
service principal; the rest are named with one untyped Graph lookup.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

from azure.core.credentials import TokenCredential

from app.db.models import RoleAssignment
from app.tools.graph_resolver import resolve_objects

Resolver = Callable[[set[str], TokenCredential], dict[str, tuple[str, str | None]]]


def origin_fields(ra: Any) -> dict[str, Any]:
    """Origin columns for a new ``RoleAssignment`` from an SDK assignment."""
    created_by = getattr(ra, "created_by", None)
    return {
        "created_on": _as_datetime(getattr(ra, "created_on", None)),
        "created_by": created_by.lower() if isinstance(created_by, str) and created_by else None,
        "updated_on": _as_datetime(getattr(ra, "updated_on", None)),
    }


def name_creators(
    rows: Iterable[RoleAssignment],
    principal_types: Mapping[str, str],
    principal_names: Mapping[str, str],
    credential: TokenCredential,
    resolver: Resolver = resolve_objects,
) -> int:
    """Fill ``created_by_name`` / ``created_by_type``; returns how many rows got a name.

    ``principal_types`` / ``principal_names`` are the snapshot's own
    ``{object id: type}`` and ``{object id: name}``; only creators missing
    from them go to Graph.
    """
    rows = [r for r in rows if r.created_by]
    unknown = {r.created_by for r in rows if r.created_by not in principal_names}
    looked_up = {k.lower(): v for k, v in resolver(unknown, credential).items()} if unknown else {}
    named = 0
    for r in rows:
        if r.created_by in principal_names:
            r.created_by_name = principal_names[r.created_by]
            r.created_by_type = principal_types.get(r.created_by)
        elif r.created_by in looked_up:
            r.created_by_name, r.created_by_type = looked_up[r.created_by]
        else:
            continue
        named += 1
    return named


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
