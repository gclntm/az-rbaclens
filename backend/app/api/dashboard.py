"""Home's overview: current-state aggregates (one check per place)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.overview import build_overview
from app.db.session import get_session
from app.schemas.dashboard import Overview

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=Overview)
def get_overview(
    session: Annotated[Session, Depends(get_session)],
    attention_limit: int = Query(8, ge=1, le=50),
    freshness_days: int = Query(30, ge=1, le=365),
) -> Overview:
    """Landing-page aggregates over the *current* RA set (one snapshot per scope).

    Nothing here double counts superseded checks.
    """
    return build_overview(session, attention_limit, freshness_days)
