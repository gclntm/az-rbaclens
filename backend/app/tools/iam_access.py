"""IAM access scan service: ``run_scan``, the worker behind a check.

The phases live in their own modules: ``scan_walk`` (discover places),
``scan_list`` (list grants and resolve names), ``scan_persist`` (save role
definitions and group members); ``scope_ids`` holds the scope-id helpers.

Walks an Azure RBAC scope hierarchy and lists role assignments at every
scope below the scan root. The scan root can be a management group, a
subscription, a resource group, or an individual resource — the walker
dispatches based on the scope type:

- mgmt group → child mgmt groups + subscriptions → resource groups
- subscription → resource groups
- resource group → just the RG itself (MVP doesn't enumerate resources)
- resource → just the resource itself

Designed to run as a FastAPI BackgroundTask. Opens its own DB session so it
doesn't share state with the request that started it.

The scan runs in three phases so the UI can show real progress:

    1. discover  — walk the hierarchy, build a list of (scope, scope_level)
                   targets and a parent-of map. ``progress_total`` is unknown
                   during this phase.
    2. list      — for each target, call ``role_assignments.list_for_scope``
                   and increment ``progress_done``. Inherited assignments
                   (whose ``scope`` sits above what we walked, e.g. tenant
                   root) trigger an ancestor-chain lookup so the resulting
                   tree includes those parents.
    3. resolve   — fill ``principal_name`` via Microsoft Graph (best-effort).

Resolution:
- ``scope_name`` is computed from subscription / mgmt-group display names
  captured during phase 1 plus any ancestor lookups in phase 2.
- ``principal_name`` is filled in phase 3 (best-effort; falls back to the
  principal ID when the SP lacks ``Directory.Read.All``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from azure.core.credentials import TokenCredential

from app.core.azure import get_credential
from app.core.jobs import finish_job, mark_job_failed, start_job
from app.db import session as db_session
from app.db.models import (
    RoleAssignment,
    Scan,
)
from app.tools.graph_groups import resolve_group_memberships
from app.tools.graph_resolver import resolve_principals
from app.tools.scan_list import collect_assignments
from app.tools.scan_persist import persist_group_memberships, upsert_role_definitions
from app.tools.scan_walk import Hierarchy

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# Public entry point (called by BackgroundTasks)
# --------------------------------------------------------------------------------------


def run_scan(scan_id: int) -> None:
    """Run a scan to completion. Marks the scan done/failed and persists results."""
    # Resolved at call time (not import time) so test fixtures that swap
    # ``app.db.session.SessionLocal`` are honoured — same reason the
    # sweeper imports lazily.
    with db_session.SessionLocal() as session:
        scan = session.get(Scan, scan_id)
        if scan is None:
            logger.warning("scan %s not found, aborting", scan_id)
            return

        if not start_job(session, Scan, scan_id):
            return
        scan.progress_done = 0
        scan.progress_total = None
        scan.progress_message = "Starting…"
        scan.hierarchy = None
        session.commit()

        def on_progress(done: int, total: int | None, message: str) -> None:
            scan.progress_done = done
            scan.progress_total = total
            scan.progress_message = message
            scan.heartbeat_at = datetime.now(UTC)
            session.commit()

        try:
            credential = get_credential()
            (
                assignments,
                hierarchy,
                role_definitions,
                group_memberships,
            ) = collect_assignments(scan.scope, scan.scope_level, credential, on_progress)
            pim = _collect_pim_safely(scan, hierarchy, credential, role_definitions)
            _apply_pim(assignments, pim, group_memberships, credential)
            for ra in assignments:
                ra.scan_id = scan.id
                session.add(ra)
            for eligibility in pim.eligibilities:
                eligibility.scan_id = scan.id
                session.add(eligibility)
            scan.eligibility_status = pim.status
            scan.eligibility_error = pim.error
            scan.hierarchy = hierarchy
            upsert_role_definitions(session, role_definitions)
            persist_group_memberships(session, scan.id, group_memberships)
            total_members = sum(len(members) for members in group_memberships.values())
            scan.progress_message = f"Done — {len(assignments)} role assignments"
            scan.heartbeat_at = datetime.now(UTC)
            # Single commit for results + terminal status: either the
            # whole result set lands with ``done``, or nothing does. Nothing
            # lands either when the sweeper already marked the scan failed.
            if not finish_job(session, Scan, scan_id):
                return
            logger.info(
                "scan %s done — %d role assignments, %d scope nodes, %d group members",
                scan_id,
                len(assignments),
                len(hierarchy),
                total_members,
            )
            # Derived data: registries, then coverage cache + findings.
            # Never fails the scan — the result set is already committed
            # as ``done``.
            _update_registries_safely(session, scan)
            _compute_findings_safely(session, scan_id)
            _record_changes_safely(session, scan_id)
        except Exception as exc:
            logger.exception("scan %s failed", scan_id)
            # Discards any pending rows (a failed scan never carries a
            # partial result set) and records the failure in a fresh
            # transaction — see app.core.jobs.
            mark_job_failed(session, Scan, scan_id, f"{type(exc).__name__}: {exc}")


def _collect_pim_safely(
    scan: Scan,
    hierarchy: Hierarchy,
    credential: TokenCredential,
    role_definitions: dict[str, dict[str, Any]],
):
    """PIM eligibility + active schedule types; never fails the snapshot."""
    from app.tools import pim as pim_tool

    try:
        return pim_tool.collect_pim(
            scan.scope, scan.scope_level, hierarchy, credential, role_definitions
        )
    except Exception as exc:
        logger.exception("PIM collection failed for scan %s", scan.id)
        return pim_tool.PimResult(status="failed", error=f"{type(exc).__name__}: {exc}")


def _apply_pim(
    assignments: list[RoleAssignment],
    pim: Any,
    group_memberships: dict[str, Any],
    credential: TokenCredential,
) -> None:
    """Stamp assignment type / end on active rows; name eligible principals
    and resolve eligible groups' members like the rest of the snapshot."""
    for ra in assignments:
        guid = ((ra.raw or {}).get("id") or "").rstrip("/").rsplit("/", 1)[-1].lower()
        if guid in pim.active:
            ra.assignment_type, ra.end_at = pim.active[guid]
    if not pim.eligibilities:
        return
    try:
        known = {ra.principal_id: ra.principal_name for ra in assignments if ra.principal_name}
        missing = {
            e.principal_id: e.principal_type
            for e in pim.eligibilities
            if e.principal_id not in known
        }
        known.update(resolve_principals(missing, credential) if missing else {})
        for e in pim.eligibilities:
            e.principal_name = known.get(e.principal_id)
        groups = sorted(
            {e.principal_id for e in pim.eligibilities if e.principal_type == "Group"}
            - set(group_memberships)
        )
        if groups:
            group_memberships.update(resolve_group_memberships(groups, credential))
    except Exception:
        logger.exception("naming eligible principals failed; eligibilities kept unnamed")


def _update_registries_safely(session: Any, scan: Scan) -> None:
    """Upsert ``scopes`` / ``principals`` for a just-completed scan.

    Lazy import for the same cycle reason as findings. Logged and
    swallowed on failure; ``POST /settings/registry/rebuild`` recovers.
    """
    try:
        from app.core.registry import update_registries_for_scan

        update_registries_for_scan(session, scan)
        session.commit()
    except Exception:
        logger.exception("registry update failed for scan %s (scan stays done)", scan.id)
        session.rollback()


def _compute_findings_safely(session: Any, scan_id: int) -> None:
    """Build the coverage cache + findings for a just-completed scan.

    Lazy import: ``app.core.findings`` imports the coverage builder,
    which imports this module for ``normalize_scope`` /
    ``role_definition_guid``. A failure here is logged and swallowed —
    the scan itself stays ``done``; ``POST …/findings/recompute`` can
    retry.
    """
    try:
        from app.core.findings import compute_scan_findings

        compute_scan_findings(session, scan_id)
    except Exception:
        logger.exception("findings computation failed for scan %s (scan stays done)", scan_id)
        session.rollback()


def _try_entra_audit(session: Any) -> None:
    """Can the Entra ID audit log be read? Tried at most once a day while it
    can't, so its collection starts by itself once the permission is granted.
    Never fails the scan."""
    from app.core.config import settings

    if not settings.default_schedules:
        return
    try:
        from app.tools.entra_audit import refresh_status_if_due

        refresh_status_if_due(session, datetime.now(UTC))
    except Exception:
        logger.exception("Entra ID audit log could not be tried")
        session.rollback()


def _record_changes_safely(session: Any, scan_id: int) -> None:
    """Changes feed entries for a just-completed check, and pull schedules
    for subscriptions it found for the first time. Never fails the scan."""
    try:
        from app.core.changes import record_changes_for_scan
        from app.core.default_schedules import ensure_default_schedules

        record_changes_for_scan(session, scan_id)
        _try_entra_audit(session)
        ensure_default_schedules(session, datetime.now(UTC))
    except Exception:
        logger.exception("changes feed update failed for scan %s (scan stays done)", scan_id)
        session.rollback()
    try:
        from app.core.responsible import graph_lookup, refresh_suggestions

        refresh_suggestions(session, graph_lookup())
    except Exception:
        logger.exception("responsible-person suggestions not refreshed after scan %s", scan_id)
        session.rollback()
    try:
        from app.core.reviews import open_and_sync

        open_and_sync(session)
    except Exception:
        logger.exception("monthly reviews not updated after scan %s", scan_id)
        session.rollback()
