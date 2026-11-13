"""Findings: the review worklist derived from a scan's coverage result.

A *finding* is one reviewable statement about one role assignment —
"Owner at subscription scope", "0 % of granted actions used in 90 d",
"redundant: covered by an ancestor grant", … — with a severity, the
evidence behind it, and a suggested action. Findings are recomputed
from scratch for a scan (``compute_scan_findings``) at snapshot end,
when a new activity pull lands for one of its subscriptions, or on
demand; each run replaces the scan's previous rows.

Review decisions live in a separate table keyed by **fingerprint**
(``sha256(scope | role GUID | principal | category)``), not by finding
id, so "reviewed" / "accepted risk" / "snoozed" survives re-scans and
re-classification: the same assignment flagged for the same reason in
snapshot #42 carries the decision made on snapshot #41.

Categories and severity rules are in :func:`derive_findings` (pure —
takes a ``RoleCoverageResult``, returns drafts). Everything DB-shaped
is in :func:`compute_scan_findings`.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.activity_targets import MGMT_GROUP_PREFIX, MGMT_GROUPS
from app.core.scan_coverage import (
    build_scan_coverage,
    latest_done_scan_ids,
    subscription_id_of_scope,
)
from app.db.models import ActivityPull, Finding, RoleAssignment, Scan, ScanCoverage
from app.schemas.iam_access import RoleCoverageResult, RoleCoverageRow

logger = logging.getLogger(__name__)

HIGH, MEDIUM, LOW = "high", "medium", "low"
SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}

# Within one severity, what a reviewer should see first: identity
# problems, then blast radius, then waste, then change, then hygiene.
CATEGORY_PRIORITY = {
    c: i
    for i, c in enumerate(
        (
            "unresolved_principal",
            "high_risk_broad_scope",
            "unused",
            "drift_added",
            "over_broad",
            "custom_replaceable",
            "redundant",
            "membership_unknown",
        )
    )
}


def finding_sort_key(severity: str, category: str, title: str) -> tuple[int, int, str]:
    """Severity, then category priority, then title — used by every listing."""
    return (SEVERITY_ORDER.get(severity, 9), CATEGORY_PRIORITY.get(category, 99), title.lower())


CATEGORIES = (
    "high_risk_broad_scope",
    "unresolved_principal",
    "unused",
    "over_broad",
    "custom_replaceable",
    "redundant",
    "membership_unknown",
    "drift_added",
)

BROAD_LEVELS = {"mgmt_group", "subscription"}


@dataclass
class FindingDraft:
    role_assignment_id: int
    scope: str
    role_definition_id: str
    principal_id: str
    category: str
    severity: str
    title: str
    detail: str
    suggestion: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Fingerprint
# --------------------------------------------------------------------------------------


def fingerprint(scope: str, role_definition_id: str, principal_id: str, category: str) -> str:
    """Stable identity of a finding across scans.

    Inputs are normalised (lowercased scope / principal, bare role
    GUID) so Azure's casing drift and the ``/subscriptions/<sub>/…``
    prefix on role-definition ids never produce a new fingerprint for
    the same grant.
    """
    from app.tools.scope_ids import role_definition_guid

    raw = "|".join(
        [
            scope.strip().lower(),
            role_definition_guid(role_definition_id).lower(),
            principal_id.strip().lower(),
            category,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def grant_fingerprint(scope: str, role_definition_id: str, principal_id: str) -> str:
    """Stable identity of a *grant* across snapshots: one role, one identity, one place.

    The finding fingerprint without the category, so it survives
    re-scans the same way. Used as the grant id in URLs and, later, by
    monthly reviews.
    """
    from app.tools.scope_ids import role_definition_guid

    raw = "|".join(
        [
            scope.strip().lower(),
            role_definition_guid(role_definition_id).lower(),
            principal_id.strip().lower(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _identity(scope: str, role_definition_id: str, principal_id: str) -> tuple[str, str, str]:
    from app.tools.scope_ids import role_definition_guid

    return (scope.lower(), role_definition_guid(role_definition_id).lower(), principal_id.lower())


# --------------------------------------------------------------------------------------
# Derivation (pure)
# --------------------------------------------------------------------------------------


def _label(row: RoleCoverageRow) -> tuple[str, str, str]:
    role = row.role_name or row.role_definition_id.rsplit("/", 1)[-1]
    principal = row.principal_name or row.principal_id
    scope = row.scope_name or row.scope.rstrip("/").rsplit("/", 1)[-1] or row.scope
    return role, principal, scope


def _has_activity_data(row: RoleCoverageRow, coverage: RoleCoverageResult) -> bool:
    """True when the window had events we could have matched this RA against."""
    sub = subscription_id_of_scope(row.scope)
    if sub:
        return sub not in set(coverage.subscriptions_without_events)
    # MG-level RA: inherited by every sub in the scan. Any events at all count.
    return coverage.events_considered > 0


def _row_findings(
    row: RoleCoverageRow,
    coverage: RoleCoverageResult,
    window_days: int,
    previous_identities: set[tuple[str, str, str]] | None,
) -> list[FindingDraft]:
    """All findings for one coverage row (see ``derive_findings``)."""
    drafts: list[FindingDraft] = []
    role, principal, scope = _label(row)
    high = row.role_risk == "high"
    base_evidence: dict[str, Any] = {
        "role_risk": row.role_risk,
        "role_type": row.role_type,
        "scope_level": row.scope_level,
        "window_days": window_days,
    }

    def add(
        category: str,
        severity: str,
        detail: str,
        suggestion: str | None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        drafts.append(
            FindingDraft(
                role_assignment_id=row.assignment_id,
                scope=row.scope,
                role_definition_id=row.role_definition_id,
                principal_id=row.principal_id,
                category=category,
                severity=severity,
                title=f"{role} → {principal} on {scope}",
                detail=detail,
                suggestion=suggestion,
                evidence={**base_evidence, **(extra or {})},
            )
        )

    if high and row.scope_level in BROAD_LEVELS:
        add(
            "high_risk_broad_scope",
            HIGH,
            f"{role} is a powerful role, given on a {row.scope_level.replace('_', ' ')}: "
            "it applies to everything inside.",
            "Check that this power is needed everywhere below; prefer a narrower role, "
            "or give it on a smaller place.",
        )

    if not row.principal_name:
        add(
            "unresolved_principal",
            HIGH if high else MEDIUM,
            "Entra ID does not know this identity: it was probably deleted. A grant "
            "to a deleted identity can come back to life if its id is ever reused.",
            "Remove the grant, or confirm the identity still exists in Entra ID.",
        )

    membership_unknown = row.principal_type == "Group" and row.member_count is None
    if membership_unknown:
        add(
            "membership_unknown",
            MEDIUM,
            "The group's members could not be read, so who really has this access, "
            "and what they changed, is unknown.",
            "Give the app registration GroupMember.Read.All; the next daily check reads the group.",
        )

    if row.covered_by:
        add(
            "redundant",
            MEDIUM if high else LOW,
            "The same identity already holds an equal or stronger role on a place "
            "above: "
            + "; ".join(
                f"{c.role_name or 'role'} at {c.scope_name or c.scope}" for c in row.covered_by
            )
            + ".",
            "Remove this grant: the one above already applies here.",
            {
                "covered_by": [
                    {
                        "assignment_id": c.assignment_id,
                        "scope": c.scope,
                        "scope_name": c.scope_name,
                        "role_name": c.role_name,
                    }
                    for c in row.covered_by
                ]
            },
        )

    has_data = _has_activity_data(row, coverage) and not membership_unknown
    usage_evidence = {
        "coverage_pct": row.coverage_pct,
        "granted_pattern_count": row.granted_pattern_count,
        "used_pattern_count": row.used_pattern_count,
        "observed_op_count": row.observed_op_count,
        "unused_patterns": row.unused_patterns,
        "top_operations": [t.model_dump() for t in row.top_operations[:3]],
        "recommendations": [r.model_dump() for r in row.recommendations],
    }
    if row.principal_type == "Group" and row.member_count == 0:
        # Same category (and fingerprint) as "no change", so decisions carry over;
        # but an empty group has nobody to ask: its access is unused for certain.
        add(
            "unused",
            HIGH if high else MEDIUM,
            "The group has no members in Entra ID, so nobody uses this access.",
            "Remove the grant, or the group if nothing else needs it: anyone added to "
            "the group later gets this access at once.",
            usage_evidence,
        )
    elif has_data and row.granted_pattern_count > 0 and row.coverage_pct == 0:
        add(
            "unused",
            HIGH if high else MEDIUM,
            f"No change recorded in {window_days} days with any of the role's "
            f"{row.granted_pattern_count} permissions.",
            "Ask before removing: Azure never records reads, so someone who only "
            "looks or reads data shows no change.",
            usage_evidence,
        )
    elif has_data and row.recommendations and 0 < row.coverage_pct < 100:
        best = row.recommendations[0]
        if row.role_type == "CustomRole":
            add(
                "custom_replaceable",
                MEDIUM,
                f"{row.coverage_pct} % of this custom role was used for changes; built-in "
                f"{best.role_name} allows every change that was made.",
                f"Swap to {best.role_name}, then retire the custom definition.",
                usage_evidence,
            )
        else:
            add(
                "over_broad",
                MEDIUM,
                f"{row.coverage_pct} % of the role was used for changes; "
                f"{best.role_name} allows all {row.observed_op_count} kinds of change that "
                f"were made, with {best.patterns_saved} fewer permissions.",
                f"Replace with {best.role_name}.",
                usage_evidence,
            )

    if previous_identities is not None and (
        _identity(row.scope, row.role_definition_id, row.principal_id) not in previous_identities
    ):
        add(
            "drift_added",
            HIGH if high else LOW,
            "This grant is new since the previous daily check of this place.",
            "Check that there is a change request for it.",
        )
    return drafts


def derive_findings(
    coverage: RoleCoverageResult,
    *,
    window_days: int,
    previous_identities: set[tuple[str, str, str]] | None = None,
) -> list[FindingDraft]:
    """Turn coverage rows into findings. Pure; no DB.

    ``previous_identities`` — ``(scope, role GUID, principal)`` tuples
    from the previous completed scan of the same root, or ``None`` when
    there is no previous scan (then no ``drift_added`` findings).
    """
    drafts: list[FindingDraft] = []
    for row in coverage.rows:
        drafts.extend(_row_findings(row, coverage, window_days, previous_identities))

    drafts.sort(key=lambda d: finding_sort_key(d.severity, d.category, d.title))
    return drafts


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------


def previous_done_scan(session: Session, scan: Scan) -> Scan | None:
    """The most recent completed scan of the same root before ``scan``."""
    return session.scalars(
        select(Scan)
        .where(Scan.scope == scan.scope)
        .where(Scan.status == "done")
        .where(Scan.id != scan.id)
        .where(
            (Scan.created_at < scan.created_at)
            | ((Scan.created_at == scan.created_at) & (Scan.id < scan.id))
        )
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(1)
    ).first()


def compute_scan_findings(session: Session, scan_id: int, window_days: int = 90) -> ScanCoverage:
    """Build coverage for a completed scan, cache it, and replace its findings.

    Raises ``ValueError`` for an unknown or non-``done`` scan. Commits.
    """
    scan = session.get(Scan, scan_id)
    if scan is None:
        raise ValueError(f"scan {scan_id} not found")
    if scan.status != "done":
        raise ValueError(f"scan {scan_id} is {scan.status}; findings need a completed scan")

    coverage = build_scan_coverage(session, scan, window_days)

    previous = previous_done_scan(session, scan)
    previous_identities: set[tuple[str, str, str]] | None = None
    if previous is not None:
        previous_identities = {
            _identity(scope, rd, pid)
            for scope, rd, pid in session.execute(
                select(
                    RoleAssignment.scope,
                    RoleAssignment.role_definition_id,
                    RoleAssignment.principal_id,
                ).where(RoleAssignment.scan_id == previous.id)
            ).all()
        }

    drafts = derive_findings(
        coverage, window_days=window_days, previous_identities=previous_identities
    )
    now = datetime.now(UTC)

    session.execute(delete(Finding).where(Finding.scan_id == scan_id))
    seen: set[str] = set()
    for d in drafts:
        fp = fingerprint(d.scope, d.role_definition_id, d.principal_id, d.category)
        if fp in seen:
            continue
        seen.add(fp)
        session.add(
            Finding(
                scan_id=scan_id,
                role_assignment_id=d.role_assignment_id,
                fingerprint=fp,
                category=d.category,
                severity=d.severity,
                title=d.title,
                detail=d.detail,
                suggestion=d.suggestion,
                evidence=d.evidence,
                computed_at=now,
            )
        )

    cache = session.get(ScanCoverage, scan_id)
    if cache is None:
        cache = ScanCoverage(scan_id=scan_id)
        session.add(cache)
    cache.window_days = window_days
    cache.computed_at = now
    cache.payload = coverage.model_dump(mode="json")
    cache.previous_scan_id = previous.id if previous else None
    # Registry side-effect: the coverage pass is the only place events
    # get attributed to principals, so it owns ``last_activity_at``.
    from app.core.registry import record_principal_activity

    record_principal_activity(session, coverage.principal_last_activity)
    session.commit()
    logger.info(
        "findings for scan %s: %d findings, %d coverage rows, previous scan %s",
        scan_id,
        len(seen),
        len(coverage.rows),
        previous.id if previous else None,
    )
    return cache


def recompute_for_subscription(session: Session, subscription_id: str) -> list[int]:
    """Recompute findings for every latest completed scan that covers ``subscription_id``."""
    return recompute_for_subscriptions(session, {subscription_id})


def recompute_for_subscriptions(session: Session, subscription_ids: set[str]) -> list[int]:
    """Recompute findings once for every latest completed scan that covers any of the subscriptions.

    Called after activity pulls land so usage-based findings reflect
    the new events. Each scan is independent; one failure is logged and
    does not stop the others. Returns the scan ids that were recomputed.
    """
    subs = {s.lower() for s in subscription_ids}
    sub_scopes = {f"/subscriptions/{s}" for s in subs}
    done: list[int] = []
    for scan_id in sorted(latest_done_scan_ids(session)):
        scan = session.get(Scan, scan_id)
        if scan is None:
            continue
        covers = subscription_id_of_scope(scan.scope) in subs or not sub_scopes.isdisjoint(
            scan.hierarchy or {}
        )
        # New events from the management groups' logs concern every check that reaches one.
        if not covers and MGMT_GROUPS in subs:
            covers = any(
                s.lower().startswith(MGMT_GROUP_PREFIX)
                for s in (scan.scope, *(scan.hierarchy or {}))
            )
        if not covers:
            continue
        cache = session.get(ScanCoverage, scan_id)
        window_days = cache.window_days if cache is not None else 90
        try:
            compute_scan_findings(session, scan_id, window_days)
            done.append(scan_id)
        except Exception:
            logger.exception("findings recompute failed for scan %s", scan_id)
            session.rollback()
    return done


# --------------------------------------------------------------------------------------
# Recompute after new activity, once per round of pulls
# --------------------------------------------------------------------------------------

# Subscriptions with new events since the last recompute, and when the oldest
# of them arrived. In memory: after a restart the next pull or check refreshes.
_dirty: set[str] = set()
_dirty_since: datetime | None = None
_dirty_lock = threading.Lock()
# Recompute even while pulls keep running once new events have waited this long.
RECOMPUTE_MAX_WAIT = timedelta(minutes=10)


def request_recompute(subscription_id: str) -> None:
    """Note that ``subscription_id`` got new events; :func:`flush_recompute` does the work."""
    global _dirty_since
    with _dirty_lock:
        _dirty.add(subscription_id.lower())
        if _dirty_since is None:
            _dirty_since = datetime.now(UTC)


def flush_recompute(session: Session, now: datetime | None = None) -> list[int]:
    """Recompute findings for the subscriptions that got new events, once for all of them.

    Waits while a pull is still running (its events would need another
    pass), but not longer than :data:`RECOMPUTE_MAX_WAIT`. Called from the
    scheduler tick. Returns the scan ids recomputed.
    """
    global _dirty_since
    now = now or datetime.now(UTC)
    with _dirty_lock:
        if not _dirty:
            return []
        waited = now - (_dirty_since or now)
    if waited < RECOMPUTE_MAX_WAIT:
        busy = session.scalar(
            select(func.count())
            .select_from(ActivityPull)
            .where(ActivityPull.status.in_(("pending", "running")))
        )
        if busy:
            return []
    with _dirty_lock:
        subs = set(_dirty)
        _dirty.clear()
        _dirty_since = None
    return recompute_for_subscriptions(session, subs)
