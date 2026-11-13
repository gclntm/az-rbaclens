"""``/report``: the assessment report for a scope, its CSV annex, and saved assessments.

Admins see every scope; a signed-in person who isn't an admin sees the
report for their own places only (the visibility guard checks ``scope``).
Saving an assessment is for admins.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.registry import normalise_scope_param
from app.core.advice import advise_grant
from app.core.advice import context as advice_context
from app.core.csv_safe import safe_writer
from app.core.current_view import current_view
from app.core.report import ReportScope, build_report
from app.core.security import require_admin, require_auth, who_is
from app.core.visibility import vis_of
from app.db.models import Assessment, Scope
from app.db.session import get_session
from app.schemas.report import AssessmentRead, AssessmentWrite, Report

router = APIRouter(prefix="/report", tags=["report"])
Claims = Annotated[dict[str, Any] | None, Depends(require_auth)]
ScopeParam = Annotated[
    str | None, Query(max_length=1000, description="ARM path; empty = the whole tenant")
]


def _scope_or_404(session: Session, request: Request, raw: str | None) -> str | None:
    if not raw or not raw.strip().strip("/"):
        if vis_of(request) is not None:
            raise HTTPException(
                status_code=403, detail="pick one of your places: the whole tenant is for admins"
            )
        return None
    sid = normalise_scope_param(raw)
    if session.get(Scope, sid) is None:
        raise HTTPException(
            status_code=404, detail="this place is not known to any completed check"
        )
    return sid


@router.get("", response_model=Report)
def get_report(
    request: Request, session: Annotated[Session, Depends(get_session)], scope: ScopeParam = None
) -> Report:
    return build_report(session, _scope_or_404(session, request, scope))


@router.get("/annex")
def report_annex(
    request: Request, session: Annotated[Session, Depends(get_session)], scope: ScopeParam = None
) -> StreamingResponse:
    """Every grant in the scope with its capability, how it is given, and its advice."""
    sid = _scope_or_404(session, request, scope)
    view = current_view(session)
    ctx = advice_context(session, view)
    sc = ReportScope(view, sid)
    rows = sorted(
        (ra for ra in view.ras if sc.has(ra.scope)),
        key=lambda ra: (ra.scope.lower(), ra.principal_name or ""),
    )
    ctx.prime_groups({ra.principal_id.lower() for ra in rows if ra.principal_type == "User"})
    buf = io.StringIO()
    w = safe_writer(buf)
    w.writerow(
        [
            "place",
            "place_level",
            "who",
            "who_type",
            "object_id",
            "role",
            "can",
            "how",
            "since",
            "given_by",
            "advice_rule",
            "advice_weight",
            "advice",
            "iso_controls",
        ]
    )
    for ra in rows:
        adv = advise_grant(ctx, ra)
        how = (
            "pim"
            if ra.assignment_type == "Activated"
            else ("time-bound" if ra.end_at else "standing")
        )
        w.writerow(
            [
                view.name.get(ra.scope.lower()) or ra.scope_name or ra.scope,
                ra.scope_level,
                ra.principal_name or "",
                ra.principal_type,
                ra.principal_id.lower(),
                ra.role_name or "",
                view.capability_of(ra).level,
                how,
                ra.created_on.date().isoformat() if ra.created_on else "",
                ra.created_by_name or "",
                adv.rule,
                adv.weight,
                adv.title,
                " ".join(f"A.{c}" for c in adv.controls),
            ]
        )
    name = (sid.rsplit("/", 1)[-1] if sid else "tenant") + f"-{datetime.now(UTC):%Y%m%d}"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="rbaclens-annex-{name}.csv"'},
    )


def _read(a: Assessment) -> AssessmentRead:
    return AssessmentRead(
        id=a.id,
        scope_id=a.scope_id,
        scope_name=a.scope_name,
        created_at=a.created_at,
        created_by_name=a.created_by_name,
        access_as_of=a.access_as_of,
        note=a.note,
        statuses=a.statuses,
        numbers=a.numbers,
    )


@router.get("/assessments", response_model=list[AssessmentRead])
def list_assessments(
    request: Request, session: Annotated[Session, Depends(get_session)], scope: ScopeParam = None
) -> list[AssessmentRead]:
    """Saved assessments of one scope, newest first."""
    sid = _scope_or_404(session, request, scope)
    stmt = select(Assessment).order_by(Assessment.created_at.desc(), Assessment.id.desc())
    stmt = stmt.where(Assessment.scope_id.is_(None) if sid is None else Assessment.scope_id == sid)
    return [_read(a) for a in session.scalars(stmt).all()]


@router.post("/assessments", response_model=AssessmentRead, dependencies=[Depends(require_admin)])
def save_assessment(
    payload: AssessmentWrite,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    claims: Claims,
) -> AssessmentRead:
    """Keep this report's statuses and numbers, read-only, as evidence."""
    sid = _scope_or_404(session, request, payload.scope)
    report = build_report(session, sid)
    oid, name, _ = who_is(session, claims)
    row = Assessment(
        scope_id=sid,
        scope_name=report.scope_name,
        created_at=report.generated_at,
        created_by=oid,
        created_by_name=name or "Admin",
        access_as_of=report.access_as_of,
        note=(payload.note or "").strip() or None,
        statuses={c.control: c.status for c in report.controls},
        numbers=report.numbers,
    )
    session.add(row)
    session.commit()
    return _read(row)
