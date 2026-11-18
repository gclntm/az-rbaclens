"""Findings and review decisions."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import _actor_from_request
from app.core.current_view import FindingRef, current_view
from app.core.findings import compute_scan_findings, finding_sort_key
from app.core.visibility import vis_of
from app.db.models import Finding, ReviewDecision, RoleAssignment, Scan, ScanCoverage
from app.db.session import get_session
from app.schemas.findings import (
    BulkDecisionResult,
    BulkDecisionWrite,
    CurrentFindingRead,
    CurrentFindings,
    FindingRead,
    FindingsResponse,
    FindingsSummary,
    ReviewDecisionRead,
    ReviewDecisionWrite,
    finding_status,
)

router = APIRouter(prefix="/iam-access", tags=["findings"])
# Cross-scope worklist: one finding per current RA (see app.core.current_view).
global_router = APIRouter(tags=["findings"])


def _get_scan_or_404(session: Session, scan_id: int) -> Scan:
    scan = session.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail=f"check {scan_id} not found")
    return scan


def _load(session: Session, scan_id: int) -> FindingsResponse:
    rows = session.execute(
        select(Finding, ReviewDecision)
        .outerjoin(ReviewDecision, ReviewDecision.fingerprint == Finding.fingerprint)
        .where(Finding.scan_id == scan_id)
    ).all()
    now = datetime.now(UTC)
    items: list[FindingRead] = []
    for finding, decision in rows:
        dec = ReviewDecisionRead.model_validate(decision) if decision is not None else None
        item = FindingRead.model_validate(finding)
        item.decision = dec
        item.status = finding_status(dec, now)
        items.append(item)
    items.sort(key=lambda f: finding_sort_key(f.severity, f.category, f.title))

    summary = FindingsSummary(
        total=len(items),
        open=sum(1 for f in items if f.status == "open"),
        by_severity=dict(Counter(f.severity for f in items)),
        by_category=dict(Counter(f.category for f in items)),
        by_status=dict(Counter(f.status for f in items)),
        grants=len({_grant_key(f) for f in items}),
        open_grants=len({_grant_key(f) for f in items if f.status == "open"}),
    )
    cache = session.get(ScanCoverage, scan_id)
    return FindingsResponse(
        scan_id=scan_id,
        computed_at=cache.computed_at if cache else None,
        window_days=cache.window_days if cache else None,
        previous_scan_id=cache.previous_scan_id if cache else None,
        summary=summary,
        findings=items,
    )


@router.get("/scans/{scan_id}/findings", response_model=FindingsResponse)
def list_findings(
    scan_id: int,
    session: Annotated[Session, Depends(get_session)],
    severity: str | None = Query(None, pattern="^(high|medium|low)$"),
    category: str | None = None,
    status: str | None = Query(None, pattern="^(open|reviewed|accepted_risk|snoozed|resolved)$"),
) -> FindingsResponse:
    """Findings for a scan with their effective review status.

    ``summary`` always describes the full set; the filters only narrow
    ``findings``. Findings are computed at snapshot end — a scan that
    pre-dates this feature returns ``computed_at: null`` until
    ``POST …/findings/recompute`` is called.
    """
    _get_scan_or_404(session, scan_id)
    response = _load(session, scan_id)
    if severity:
        response.findings = [f for f in response.findings if f.severity == severity]
    if category:
        response.findings = [f for f in response.findings if f.category == category]
    if status:
        response.findings = [f for f in response.findings if f.status == status]
    return response


@router.post("/scans/{scan_id}/findings/recompute", response_model=FindingsResponse)
def recompute_findings(
    scan_id: int,
    session: Annotated[Session, Depends(get_session)],
    window_days: int | None = Query(None, ge=1, le=365),
) -> FindingsResponse:
    """Rebuild coverage + findings for a completed scan (synchronous)."""
    scan = _get_scan_or_404(session, scan_id)
    if scan.status != "done":
        raise HTTPException(
            status_code=400,
            detail=f"check {scan_id} is {scan.status}: what needs a look is worked out once a check is done",
        )
    compute_scan_findings(session, scan_id, window_days)
    return _load(session, scan_id)


@router.put("/findings/{fingerprint}/decision", response_model=ReviewDecisionRead)
def put_decision(
    fingerprint: str,
    payload: ReviewDecisionWrite,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> ReviewDecisionRead:
    """Create or replace the reviewer's verdict for a finding fingerprint.

    The fingerprint need not currently exist in ``findings`` — a
    decision can be recorded against a finding that a later snapshot
    will raise again.
    """
    _validate_decision(payload)
    row = _write_decision(session, fingerprint, payload, *_actor_from_request(request))
    session.commit()
    session.refresh(row)
    return ReviewDecisionRead.model_validate(row)


@router.post("/findings/decisions", response_model=BulkDecisionResult)
def put_decisions_bulk(
    payload: BulkDecisionWrite,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> BulkDecisionResult:
    """Apply one verdict to many fingerprints in a single transaction."""
    _validate_decision(payload)
    actor, actor_name = _actor_from_request(request)
    fingerprints = list(dict.fromkeys(fp for fp in payload.fingerprints if fp))
    for fp in fingerprints:
        _write_decision(session, fp, payload, actor, actor_name)
    session.commit()
    return BulkDecisionResult(updated=len(fingerprints))


def _validate_decision(payload: ReviewDecisionWrite) -> None:
    if payload.decision == "snoozed" and payload.snoozed_until is None:
        raise HTTPException(status_code=400, detail="snoozed requires snoozed_until")


def _write_decision(
    session: Session,
    fingerprint: str,
    payload: ReviewDecisionWrite,
    actor: str | None,
    actor_name: str | None,
) -> ReviewDecision:
    row = session.get(ReviewDecision, fingerprint)
    if row is None:
        row = ReviewDecision(fingerprint=fingerprint)
        session.add(row)
    row.decision = payload.decision
    row.note = payload.note
    row.snoozed_until = payload.snoozed_until if payload.decision == "snoozed" else None
    row.actor = actor
    row.actor_name = actor_name
    row.review_item_id = None  # decided by hand: a review's undo leaves it alone
    row.decided_at = datetime.now(UTC)
    return row


@router.delete("/findings/{fingerprint}/decision", status_code=204)
def delete_decision(fingerprint: str, session: Annotated[Session, Depends(get_session)]) -> None:
    row = session.get(ReviewDecision, fingerprint)
    if row is None:
        raise HTTPException(status_code=404, detail="no decision recorded for this finding")
    session.delete(row)
    session.commit()


def current_finding(ra: RoleAssignment, ref: FindingRef) -> CurrentFindingRead:
    """A finding on a current RA, carrying the RA's scope / principal / role."""
    base = FindingRead.model_validate(ref.finding)
    return CurrentFindingRead(
        **base.model_dump(exclude={"status", "decision"}),
        status=ref.status,
        decision=ref.decision,
        scope=ra.scope,
        scope_name=ra.scope_name,
        scope_level=ra.scope_level,
        principal_id=ra.principal_id,
        principal_name=ra.principal_name,
        principal_type=ra.principal_type,
        role_name=ra.role_name,
    )


@global_router.get("/findings", response_model=CurrentFindings)
def list_current_findings(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    status: str | None = Query(
        None,
        pattern="^(open|reviewed|accepted_risk|snoozed|resolved|decided)$",
        description="Effective status; ``decided`` = anything but open.",
    ),
    severity: str | None = Query(None, pattern="^(high|medium|low)$"),
    category: str | None = None,
    q: str | None = Query(None, description="Substring of title, principal, role or scope."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    per: Literal["finding", "grant"] = Query(
        "finding",
        description="``grant``: page by grant, each with all its findings that match "
        "(``total``, ``offset`` and ``limit`` count grants).",
    ),
) -> CurrentFindings:
    """Every finding on the current RA set across all scopes, worst first.

    A grant seen by several overlapping snapshots appears once (from the
    snapshot that owns its scope). ``summary`` covers everything; the
    filters narrow ``findings`` / ``total`` only. ``summary`` counts findings
    (reasons), like the counts on places and people; ``grants`` and
    ``open_grants`` count the grants they are on.
    """
    view = current_view(session)
    vis = vis_of(request)
    items = [
        current_finding(ra, ref)
        for ra in view.ras
        if vis is None or vis.sees_grant(ra.scope, ra.principal_id)
        for ref in view.findings_by_ra.get(ra.id, ())
    ]
    items.sort(key=lambda f: finding_sort_key(f.severity, f.category, f.title))
    summary = FindingsSummary(
        total=len(items),
        open=sum(1 for f in items if f.status == "open"),
        by_severity=dict(Counter(f.severity for f in items)),
        by_category=dict(Counter(f.category for f in items)),
        by_status=dict(Counter(f.status for f in items)),
        grants=len({_grant_key(f) for f in items}),
        open_grants=len({_grant_key(f) for f in items if f.status == "open"}),
    )
    if status == "decided":
        items = [f for f in items if f.status != "open"]
    elif status:
        items = [f for f in items if f.status == status]
    if severity:
        items = [f for f in items if f.severity == severity]
    if category:
        items = [f for f in items if f.category == category]
    if q:
        needle = q.strip().lower()
        items = [
            f
            for f in items
            if any(
                needle in (v or "").lower()
                for v in (
                    f.title,
                    f.principal_name,
                    f.principal_id,
                    f.role_name,
                    f.scope_name,
                    f.scope,
                )
            )
        ]
    if per == "grant":
        groups: dict[tuple[int, int | str], list[CurrentFindingRead]] = {}
        for f in items:  # worst first, so a grant sits where its worst finding does
            groups.setdefault(_grant_key(f), []).append(f)
        page = list(groups.values())[offset : offset + limit]
        return CurrentFindings(
            summary=summary,
            total=len(groups),
            offset=offset,
            limit=limit,
            findings=[f for g in page for f in g],
        )
    return CurrentFindings(
        summary=summary,
        total=len(items),
        offset=offset,
        limit=limit,
        findings=items[offset : offset + limit],
    )


def _grant_key(f: CurrentFindingRead) -> tuple[int, int | str]:
    """The grant a finding is on, as the UI groups it (``findingGroups.groupByGrant``)."""
    return (f.scan_id, f.role_assignment_id if f.role_assignment_id is not None else f.fingerprint)
