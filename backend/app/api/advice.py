"""``/settings/advice``: the playbook's thresholds and break-glass identities (admins only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import advice as core
from app.db.models import Principal
from app.db.session import get_session
from app.schemas.advice import AdviceSettings, AdviceSettingsRead, BreakGlassRead

router = APIRouter(prefix="/settings/advice", tags=["advice"])


def _read(session: Session, s: AdviceSettings) -> AdviceSettingsRead:
    names = (
        dict(
            session.execute(
                select(Principal.id, Principal.display_name).where(Principal.id.in_(s.break_glass))
            ).all()
        )
        if s.break_glass
        else {}
    )
    return AdviceSettingsRead(
        **s.model_dump(),
        break_glass_names=[
            BreakGlassRead(principal_id=p, name=names.get(p)) for p in s.break_glass
        ],
    )


@router.get("", response_model=AdviceSettingsRead)
def get_settings(session: Annotated[Session, Depends(get_session)]) -> AdviceSettingsRead:
    return _read(session, core.load_settings(session))


@router.put("", response_model=AdviceSettingsRead)
def put_settings(
    payload: AdviceSettings, session: Annotated[Session, Depends(get_session)]
) -> AdviceSettingsRead:
    saved = core.save_settings(session, payload)
    session.commit()
    return _read(session, saved)
