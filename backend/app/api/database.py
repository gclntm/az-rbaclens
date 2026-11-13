"""Database settings — stats + maintenance endpoints.

Exposes read-only stats (table / index sizes, row counts) and a small
set of operational actions (VACUUM, ANALYZE, REINDEX, cleanup of old
scans). VACUUM and REINDEX cannot run inside a transaction, so the
relevant routes drop to a raw connection with ``AUTOCOMMIT``.

The stats query branches on ``dialect.name`` so SQLite-backed test
runs return a sensible (table-list-only) response instead of erroring
on Postgres-specific catalog tables.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.retention import run_retention, scope_owner_ids
from app.db.models import Scan
from app.db.session import engine as default_engine
from app.db.session import get_session
from app.schemas.database import (
    CleanupRequest,
    DatabaseStats,
    IndexStats,
    MaintenanceResult,
    RetentionPolicy,
    RetentionRun,
    TableStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/database", tags=["database"])


def _get_engine(session: Session) -> Engine:
    """Return the engine bound to ``session`` (lets tests override via the
    session-fixture monkeypatch instead of the global engine)."""
    bind = session.get_bind()
    if isinstance(bind, Engine):
        return bind
    return default_engine


# --------------------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------------------


@router.get("/stats", response_model=DatabaseStats)
def get_stats(
    session: Annotated[Session, Depends(get_session)],
) -> DatabaseStats:
    eng = _get_engine(session)
    dialect = eng.dialect.name
    if dialect == "postgresql":
        return _pg_stats(session, dialect)
    return _generic_stats(session, dialect)


def _pg_stats(session: Session, dialect: str) -> DatabaseStats:
    db_size = session.execute(text("SELECT pg_database_size(current_database())")).scalar()

    table_rows = session.execute(
        text(
            """
            SELECT
                c.relname                    AS name,
                COALESCE(s.n_live_tup, 0)    AS row_count,
                pg_total_relation_size(c.oid) AS total_size,
                pg_relation_size(c.oid)       AS table_size,
                pg_indexes_size(c.oid)        AS index_size
            FROM pg_class c
            JOIN pg_namespace n          ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_user_tables s ON s.relname = c.relname
                                            AND s.schemaname = n.nspname
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY pg_total_relation_size(c.oid) DESC
            """
        )
    ).all()

    index_rows = session.execute(
        text(
            """
            SELECT
                ix.indexname                       AS name,
                ix.tablename                       AS table_name,
                pg_relation_size(c.oid)            AS size,
                i.indisunique                      AS is_unique
            FROM pg_indexes ix
            JOIN pg_class c     ON c.relname = ix.indexname
            JOIN pg_index i     ON i.indexrelid = c.oid
            WHERE ix.schemaname = 'public'
            ORDER BY pg_relation_size(c.oid) DESC
            """
        )
    ).all()

    return DatabaseStats(
        dialect=dialect,
        database_size_bytes=int(db_size) if db_size is not None else None,
        tables=[
            TableStats(
                name=r.name,
                row_count=r.row_count,
                total_size_bytes=r.total_size,
                table_size_bytes=r.table_size,
                index_size_bytes=r.index_size,
            )
            for r in table_rows
        ],
        indexes=[
            IndexStats(
                name=r.name,
                table_name=r.table_name,
                size_bytes=r.size,
                is_unique=bool(r.is_unique),
            )
            for r in index_rows
        ],
        fetched_at=datetime.now(UTC),
    )


def _generic_stats(session: Session, dialect: str) -> DatabaseStats:
    """Fallback stats — table list + per-table row counts (no size info)."""
    eng = _get_engine(session)
    inspector_tables = list(eng.dialect.get_table_names(session.connection()))
    tables: list[TableStats] = []
    for tname in inspector_tables:
        try:
            count = session.execute(text(f'SELECT COUNT(*) FROM "{tname}"')).scalar()  # noqa: S608 - names come from the database catalogue
        except SQLAlchemyError:
            count = None
        tables.append(
            TableStats(
                name=tname,
                row_count=count,
                total_size_bytes=None,
                table_size_bytes=None,
                index_size_bytes=None,
            )
        )

    return DatabaseStats(
        dialect=dialect,
        database_size_bytes=None,
        tables=sorted(tables, key=lambda t: t.name),
        indexes=[],
        fetched_at=datetime.now(UTC),
    )


# --------------------------------------------------------------------------------------
# Maintenance
# --------------------------------------------------------------------------------------


def _run_autocommit(eng: Engine, statements: list[str]) -> None:
    """Run statements that can't live inside a transaction (VACUUM, REINDEX)."""
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for stmt in statements:
            conn.execute(text(stmt))


@router.post("/vacuum", response_model=MaintenanceResult)
def run_vacuum(
    session: Annotated[Session, Depends(get_session)],
    full: Annotated[
        bool,
        Query(
            description="VACUUM FULL: rewrite tables to return freed space to the OS. "
            "Locks each table while it is rewritten."
        ),
    ] = False,
) -> MaintenanceResult:
    eng = _get_engine(session)
    if eng.dialect.name == "sqlite":
        # SQLite VACUUM: full rebuild, also can't be inside a tx.
        started = time.perf_counter()
        _run_autocommit(eng, ["VACUUM"])
        return MaintenanceResult(
            operation="vacuum",
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail="VACUUM completed.",
        )
    if eng.dialect.name != "postgresql":
        raise HTTPException(
            status_code=501,
            detail=f"VACUUM not supported on dialect {eng.dialect.name!r}",
        )
    # End this request's transaction first. The admin check read
    # auth_settings through ``session``, so its transaction holds a share
    # lock there; VACUUM FULL (on a second, autocommit connection) needs an
    # exclusive lock on every table and would wait on this very request
    # forever — invisible to Postgres's deadlock detector, since this side
    # is idle rather than waiting on a lock.
    session.rollback()
    started = time.perf_counter()
    statement = "VACUUM (FULL, ANALYZE)" if full else "VACUUM ANALYZE"
    _run_autocommit(eng, [statement])
    return MaintenanceResult(
        operation="vacuum",
        duration_ms=int((time.perf_counter() - started) * 1000),
        detail=f"{statement} completed.",
    )


@router.post("/analyze", response_model=MaintenanceResult)
def run_analyze(
    session: Annotated[Session, Depends(get_session)],
) -> MaintenanceResult:
    eng = _get_engine(session)
    if eng.dialect.name == "sqlite":
        started = time.perf_counter()
        session.execute(text("ANALYZE"))
        session.commit()
        return MaintenanceResult(
            operation="analyze",
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail="ANALYZE completed.",
        )
    if eng.dialect.name != "postgresql":
        raise HTTPException(status_code=501, detail="ANALYZE not supported")
    started = time.perf_counter()
    session.execute(text("ANALYZE"))
    session.commit()
    return MaintenanceResult(
        operation="analyze",
        duration_ms=int((time.perf_counter() - started) * 1000),
        detail="ANALYZE completed.",
    )


@router.post("/reindex", response_model=MaintenanceResult)
def run_reindex(
    session: Annotated[Session, Depends(get_session)],
) -> MaintenanceResult:
    eng = _get_engine(session)
    if eng.dialect.name == "postgresql":
        # Same self-wait as VACUUM FULL: REINDEX CONCURRENTLY waits for
        # transactions holding locks on each table, including this request's.
        session.rollback()
        started = time.perf_counter()
        # Reindex per-table rather than the whole database — that path
        # only requires table ownership, works on the public schema
        # without superuser, and lets CONCURRENTLY apply (REINDEX
        # DATABASE CONCURRENTLY has more constraints around system
        # catalogs).
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            tables = list(
                conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars()
            )
            for tname in tables:
                # Identifier interpolated via SQL identifier quoting —
                # tablename comes from the Postgres catalog, not user input.
                conn.execute(text(f'REINDEX TABLE CONCURRENTLY "{tname}"'))
        return MaintenanceResult(
            operation="reindex",
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail=f"REINDEX (concurrently) completed for {len(tables)} table(s).",
            rows_affected=len(tables),
        )
    if eng.dialect.name == "sqlite":
        started = time.perf_counter()
        _run_autocommit(eng, ["REINDEX"])
        return MaintenanceResult(
            operation="reindex",
            duration_ms=int((time.perf_counter() - started) * 1000),
            detail="REINDEX completed.",
        )
    raise HTTPException(status_code=501, detail="REINDEX not supported")


@router.post("/cleanup-scans", response_model=MaintenanceResult)
def cleanup_old_scans(
    payload: CleanupRequest,
    session: Annotated[Session, Depends(get_session)],
) -> MaintenanceResult:
    """Delete scans older than ``older_than_days``. role_assignments cascade
    via the existing FK ON DELETE CASCADE."""
    if payload.older_than_days < 1:
        raise HTTPException(status_code=400, detail="older_than_days must be >= 1")

    cutoff = datetime.now(UTC) - timedelta(days=payload.older_than_days)
    started = time.perf_counter()

    # Never delete a snapshot some scope still treats as its latest: the
    # scope's current view would go empty until the next snapshot.
    stmt = (
        Scan.__table__.delete()
        .where(Scan.created_at < cutoff)
        .where(Scan.status.in_(("done", "failed")))
        .where(Scan.id.not_in(scope_owner_ids(session) or {-1}))
    )
    if payload.only_failed:
        stmt = stmt.where(Scan.status == "failed")
    result = session.execute(stmt)
    session.commit()

    return MaintenanceResult(
        operation="cleanup",
        duration_ms=int((time.perf_counter() - started) * 1000),
        detail=(
            f"Deleted {result.rowcount} scan(s) older than {payload.older_than_days} day(s)"
            + (" (failed only)" if payload.only_failed else "")
            + "."
        ),
        rows_affected=result.rowcount,
    )


@router.get("/retention", response_model=RetentionPolicy)
def get_retention_policy() -> RetentionPolicy:
    return RetentionPolicy(
        interval_hours=settings.retention_interval_hours,
        event_retention_days=settings.event_retention_days,
        snapshot_keep_per_scope=settings.snapshot_keep_per_scope,
        failed_snapshot_retention_days=settings.failed_snapshot_retention_days,
        audit_retention_days=settings.audit_retention_days,
        change_retention_days=settings.change_retention_days,
    )


@router.post("/retention", response_model=RetentionRun)
def apply_retention(session: Annotated[Session, Depends(get_session)]) -> RetentionRun:
    """Apply the retention policy now (the background loop runs it daily)."""
    started = time.perf_counter()
    # A page waits for this answer: fold at most a few days here, the daily loop does the rest.
    result = run_retention(session, compact_limit=20)
    return RetentionRun(duration_ms=int((time.perf_counter() - started) * 1000), **result.as_dict())
