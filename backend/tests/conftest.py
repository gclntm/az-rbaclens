"""Test fixtures.

Tests don't talk to Azure. ``app_with_sqlite`` points SQLAlchemy at an in-memory
SQLite; JSONB columns fall back to JSON through SQLAlchemy's variants.
``tests/pg`` reruns the app's tests on a migrated Postgres (``make check``).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def app_with_sqlite(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Boot the FastAPI app with SQLite-backed session + schema, no background work.

    ``tests/pg`` overrides this fixture (same name) with a Postgres-backed one, so
    the tests it imports run unchanged against the real database.
    """
    # Importing ``app.db.models`` for its side effects: the ORM mappers
    # register their tables on ``Base.metadata`` only when the module is
    # imported. Without this, ``create_all`` finds an empty metadata.
    import app.db.models  # noqa: F401
    from app.db.base import Base

    # JSONB columns use ``with_variant(JSON(), "postgresql")`` in the models,
    # so SQLite picks up the plain JSON impl automatically.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # SQLite ignores FOREIGN KEY ... ON DELETE CASCADE unless asked. The
    # models rely on DB-level cascades (``passive_deletes=True``), so
    # tests must run with enforcement on or deletes would silently
    # orphan child rows here while cascading in Postgres.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fks(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield from boot_app(monkeypatch, engine)


def boot_app(monkeypatch: pytest.MonkeyPatch, engine) -> Iterator[TestClient]:
    """The app on ``engine``, with no background work: shared by SQLite and Postgres."""
    from app.db import session as session_mod

    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    monkeypatch.setattr(session_mod, "engine", engine)
    monkeypatch.setattr(session_mod, "SessionLocal", TestSession)

    # Block real background scans — tests focus on API behavior, not the SDK call.
    from app.api import iam_access as iam_api

    monkeypatch.setattr(iam_api, "run_scan", lambda scan_id: None)

    # Same for activity pulls — tests seed events directly.
    from app.api import activity as activity_api

    monkeypatch.setattr(activity_api, "run_pull", lambda pull_id: None)

    # A check never asks Graph for sign-ins in tests (tests that need them patch it).
    from app.tools import graph_signins

    monkeypatch.setattr(graph_signins, "collect", lambda credential, user_ids: None)

    # No scheduler loop, and "Run now" dispatches to a no-op worker.
    from app.core import scheduler
    from app.core.config import settings

    monkeypatch.setattr(settings, "scheduler_tick_seconds", 0)
    # No background sweeper either: it runs in a thread, and every session
    # here shares one in-memory SQLite connection. Its functions are tested directly.
    monkeypatch.setattr(settings, "stuck_scan_sweep_seconds", 0)
    # The nightly second pass would add a pull to ticks that test something else;
    # tests of it turn it on (test_coverage.py).
    monkeypatch.setattr(settings, "second_pass_hours", 0)
    # Default schedules are tested explicitly (test_changes.py).
    monkeypatch.setattr(settings, "default_schedules", False)
    # No mail channel unless a test sets one (the test stack points at Mailpit).
    monkeypatch.setattr(settings, "notification_channel", "")
    monkeypatch.setattr(settings, "retention_interval_hours", 0)
    # The test client calls the app as "testserver" (core/request_guard.py).
    monkeypatch.setattr(settings, "extra_server_names", "testserver")

    # Delivery pauses after a failure (module state): each test starts unpaused.
    from app.core import notify

    monkeypatch.setattr(notify, "_paused_until", None)

    # PIM is read by its own SDK calls; tests that want it fake them.
    from app.tools import pim

    monkeypatch.setattr(pim, "collect_pim", lambda *_a, **_k: pim.PimResult.not_attempted())
    monkeypatch.setattr(scheduler, "worker_for", lambda kind: lambda job_id: None)

    # Override get_session so handlers use the in-memory engine.
    from app.db.session import get_session
    from app.main import app

    def override_get_session():
        with TestSession() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def signed_in(monkeypatch: pytest.MonkeyPatch):
    """Turn sign-in on; ``signed_in(oid, admin=False)`` returns headers for that person."""
    from app.core import security

    class Row:
        enabled = True
        tenant_id = "t"
        client_id = "c"
        allowed_group_ids = None
        admin_group_ids = "admins"

    def as_user(oid: str, admin: bool = False) -> dict[str, str]:
        groups = ["admins"] if admin else []
        monkeypatch.setattr(
            security, "verify_token", lambda *_a, **_k: {"oid": oid, "name": oid, "groups": groups}
        )
        return {"Authorization": "Bearer x"}

    monkeypatch.setattr(security, "load_auth_settings", lambda _s: Row())
    return as_user


def coverage_of(scan_id: int, window_days: int = 90) -> dict:
    """A check's role coverage (what each grant was used for), built as the
    findings build it; JSON-shaped like the API's models."""
    from app.core.scan_coverage import build_scan_coverage
    from app.db.models import Scan
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        return build_scan_coverage(db, db.get(Scan, scan_id), window_days).model_dump(mode="json")
