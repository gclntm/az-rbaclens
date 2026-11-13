from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.db.session import engine
from app.version import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except SQLAlchemyError:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "stage": settings.stage,
        "db": "ok" if db_ok else "unreachable",
    }
