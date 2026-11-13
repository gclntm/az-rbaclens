"""The tenant's subscriptions, for the pickers (Data & schedules, Activity Log).

Read from the management-group entity list, which also gives each
subscription its management group (``?mgmt_group=`` filters on it, with
nested groups). Results are cached in-process
with a 15-minute TTL because the underlying Azure SDK calls can take
several seconds against larger tenants and the data rarely changes.

The cache is intentionally process-local + credential-agnostic — we only
ever have one configured SP at a time. If you ever support multiple
credentials, key the cache by ``credential`` too.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Annotated, Any

from azure.mgmt.managementgroups import ManagementGroupsAPI
from fastapi import APIRouter, HTTPException, Query

from app.core.azure import get_azure_transport, get_credential
from app.schemas.scope_browser import MgmtGroupNode, SubscriptionNode
from app.tools.scope_ids import normalize_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scope-browser", tags=["scope-browser"])

CACHE_TTL_SECONDS = 15 * 60

_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, fetch: Callable[[], Any], ttl: int = CACHE_TTL_SECONDS) -> Any:
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fetch()
    _cache[key] = (now, value)
    return value


def _invalidate_cache() -> None:
    """Exposed for tests + a future 'Refresh' button."""
    _cache.clear()


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _entity_kind(entity: Any) -> str:
    """Return ``"mgmt_group"`` | ``"subscription"`` | ``"other"`` for one
    ``mg_client.entities.list()`` row."""
    t = (getattr(entity, "type", "") or "").lower()
    if "managementgroups" in t:
        return "mgmt_group"
    if "subscription" in t:
        return "subscription"
    return "other"


def _parent_name(entity: Any) -> str | None:
    """Extract the parent MG's name segment from an entity, defensively
    against the various shapes the Azure SDK exposes across versions."""
    parent = getattr(entity, "parent", None)
    if parent is None:
        return None
    name = getattr(parent, "name", None)
    if name:
        return name
    parent_id = getattr(parent, "id", None)
    if parent_id and "/managementGroups/" in parent_id:
        return parent_id.rsplit("/", 1)[-1]
    return None


def _mg_scope(name: str) -> str:
    return normalize_scope(f"/providers/Microsoft.Management/managementGroups/{name}")


def _sub_scope(sub_id: str) -> str:
    return normalize_scope(f"/subscriptions/{sub_id}")


def _fetch_entities() -> tuple[list[MgmtGroupNode], list[SubscriptionNode]]:
    """Single ``mg_client.entities.list()`` call returns the whole visible
    MG + subscription tree with parent links. We split + decorate it
    here into our own schemas.
    """
    credential = get_credential()
    mg_client = ManagementGroupsAPI(credential, transport=get_azure_transport())
    raw_entities = list(mg_client.entities.list())

    # First pass: collect by kind.
    mgs_raw: dict[str, Any] = {}
    subs_raw: dict[str, tuple[Any, str | None]] = {}
    for ent in raw_entities:
        kind = _entity_kind(ent)
        if kind == "mgmt_group":
            mgs_raw[ent.name] = ent
        elif kind == "subscription":
            subs_raw[ent.name] = (ent, _parent_name(ent))

    # Second pass: compute MG depth via parent chain.
    children: dict[str | None, list[str]] = defaultdict(list)
    for name, ent in mgs_raw.items():
        children[_parent_name(ent)].append(name)

    depth: dict[str, int] = {}
    # BFS from roots (parent=None) — roots are at depth 0.
    queue: list[tuple[str, int]] = [(n, 0) for n in children[None]]
    while queue:
        name, d = queue.pop(0)
        depth[name] = d
        for c in children.get(name, []):
            queue.append((c, d + 1))
    # Anything left (a parent we can't see) → flag as depth 0 so it
    # still renders, just at the top.
    for name in mgs_raw:
        depth.setdefault(name, 0)

    mgs: list[MgmtGroupNode] = [
        MgmtGroupNode(
            id=name,
            display_name=getattr(ent, "display_name", None) or name,
            parent_id=_parent_name(ent),
            depth=depth.get(name, 0),
            scope=_mg_scope(name),
        )
        for name, ent in mgs_raw.items()
    ]
    # Stable tree-walk order: by (parent_path, display_name) so siblings
    # appear together and within each parent they're alphabetical.
    parent_path: dict[str, list[str]] = {}

    def path_for(name: str) -> list[str]:
        if name in parent_path:
            return parent_path[name]
        p = _parent_name(mgs_raw[name])
        chain = [*path_for(p), p] if p and p in mgs_raw else []
        parent_path[name] = chain
        return chain

    mgs.sort(key=lambda n: (path_for(n.id), n.display_name.lower(), n.id))

    subs: list[SubscriptionNode] = [
        SubscriptionNode(
            id=name,
            display_name=getattr(ent, "display_name", None) or name,
            scope=_sub_scope(name),
            mgmt_group_id=parent,
        )
        for name, (ent, parent) in subs_raw.items()
    ]
    subs.sort(key=lambda s: s.display_name.lower())
    return mgs, subs


# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------


@router.get("/subscriptions", response_model=list[SubscriptionNode])
def list_subscriptions(
    mgmt_group: Annotated[str | None, Query()] = None,
) -> list[SubscriptionNode]:
    try:
        _, subs = _cached("entities", _fetch_entities)
    except Exception as exc:
        logger.exception("scope-browser: subscriptions fetch failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if mgmt_group:
        # Build a transitive descendant set so a subscription under a
        # deeply-nested MG still shows up when the user picks an
        # ancestor MG.
        try:
            mgs, _ = _cached("entities", _fetch_entities)
        except Exception:
            logger.exception(
                "scope-browser: management groups unavailable, filter shows direct children only"
            )
            mgs = []
        children: dict[str | None, list[str]] = defaultdict(list)
        for mg in mgs:
            children[mg.parent_id].append(mg.id)
        descendants: set[str] = set()
        stack = [mgmt_group]
        while stack:
            node = stack.pop()
            descendants.add(node)
            stack.extend(children.get(node, []))
        subs = [s for s in subs if s.mgmt_group_id in descendants]
    return subs


@router.post("/refresh", status_code=204)
def refresh_cache() -> None:
    """Force-clear the in-process cache. Backs the 'Refresh' affordance
    on the scope browser when the user knows the data is stale."""
    _invalidate_cache()
