"""Optional AI wording: settings (admins), status, what is sent, and the explanation itself.

``/ai/preview`` and ``/ai/explain`` take one grant or one identity the
caller may see; the visibility guard lets them through and the checks below
apply the same rules as the grant and identity pages.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core import ai_wording as core
from app.core.advice import advise_grant, advise_identity
from app.core.advice import context as advice_context
from app.core.config import settings as app_settings
from app.core.crypto import encryption_available
from app.core.visibility import vis_of
from app.db.models import Principal
from app.db.session import get_session
from app.schemas.ai import (
    AiExplanation,
    AiPreview,
    AiSettingsRead,
    AiSettingsWrite,
    AiStatus,
    AiTarget,
)

router = APIRouter(prefix="/ai", tags=["ai"])
settings_router = APIRouter(prefix="/settings/ai", tags=["ai"])
_KIND = {
    "User": "person",
    "Group": "group",
    "ForeignGroup": "group",
    "ServicePrincipal": "app",
    "MSI": "app",
    "Application": "app",
}


def _read(session: Session, s: core.AiSettings) -> AiSettingsRead:
    return AiSettingsRead(
        enabled=s.enabled,
        model=s.model,
        models=list(core.MODELS),
        key_source=core.key_source(s),
        encryption_available=encryption_available(),
        daily_limit=max(0, app_settings.ai_daily_limit),
        written_today=core.written_today(session),
    )


@settings_router.get("", response_model=AiSettingsRead)
def get_settings(session: Annotated[Session, Depends(get_session)]) -> AiSettingsRead:
    return _read(session, core.load_settings(session))


@settings_router.put("", response_model=AiSettingsRead)
def put_settings(
    payload: AiSettingsWrite, session: Annotated[Session, Depends(get_session)]
) -> AiSettingsRead:
    try:
        saved = core.save_settings(session, payload.enabled, payload.model, payload.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return _read(session, saved)


@router.get("/status", response_model=AiStatus)
def status(session: Annotated[Session, Depends(get_session)]) -> AiStatus:
    s = core.load_settings(session)
    ok = core.available(session)
    return AiStatus(available=ok, model=s.model if ok else None)


def _facts(session: Session, request: Request, target: AiTarget) -> dict:
    ctx = advice_context(session)
    vis = vis_of(request)
    if target.grant_id:
        ra = ctx.view.by_grant_id.get(target.grant_id.lower())
        if ra is None:
            raise HTTPException(
                status_code=404, detail="this grant is not in the latest daily check"
            )
        if vis is not None and not vis.sees_grant(ra.scope, ra.principal_id):
            raise HTTPException(status_code=403, detail="not available to you")
        ctx.prime_groups({ra.principal_id.lower()})
        return core.grant_facts(ctx, ra, advise_grant(ctx, ra))
    pid = (target.principal_id or "").lower()
    row = session.get(Principal, pid)
    if row is None:
        raise HTTPException(status_code=404, detail="no daily check has seen this identity")
    if vis is not None and not vis.sees_principal(pid):
        raise HTTPException(status_code=403, detail="not available to you")
    advice = advise_identity(ctx, pid)
    if vis is not None and pid not in vis.my_ids:
        for a in advice.actions:
            a.grants = [g for g in a.grants if vis.sees_grant_at(g.scope)]
        advice.actions = [a for a in advice.actions if a.grants]
    return core.identity_facts(_KIND.get(row.principal_type or "", "other"), advice)


@router.post("/preview", response_model=AiPreview)
def preview(
    target: AiTarget, request: Request, session: Annotated[Session, Depends(get_session)]
) -> AiPreview:
    """Exactly what ``/ai/explain`` would send. Sends nothing."""
    return AiPreview(
        model=core.load_settings(session).model,
        system=core.SYSTEM,
        facts=_facts(session, request, target),
    )


@router.post("/explain", response_model=AiExplanation)
def explain(
    target: AiTarget, request: Request, session: Annotated[Session, Depends(get_session)]
) -> AiExplanation:
    facts = _facts(session, request, target)
    try:
        text, model, cached = core.explain(session, facts)
    except core.AiUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except core.AiFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    session.commit()
    return AiExplanation(text=text, model=model, cached=cached)
