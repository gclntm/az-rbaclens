"""Tests on a real Postgres, run by ``make check`` (``PG_CHECK_URL`` set).

The database is migrated once from empty with Alembic, as a deployment does;
each test then starts from empty tables. ``app_with_sqlite`` is overridden
here, so the tests imported by ``test_smoke.py`` run unchanged on Postgres.
Without ``PG_CHECK_URL`` this folder is skipped.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests.conftest import boot_app

PG_URL = os.environ.get("PG_CHECK_URL", "")

if not PG_URL:
    collect_ignore_glob = ["test_*.py"]


def alembic_config():
    from alembic.config import Config

    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    return cfg


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """An empty database, migrated to head once for the whole run."""
    from alembic import command
    from app.core.config import settings

    settings.database_url = PG_URL  # alembic/env.py reads it
    engine = create_engine(PG_URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(alembic_config(), "head")
    yield engine
    engine.dispose()


def _empty_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        names = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
        ]
        if names:
            conn.execute(
                text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE")
            )


@pytest.fixture
def app_with_sqlite(monkeypatch: pytest.MonkeyPatch, pg_engine: Engine) -> Iterator[TestClient]:
    """The same app as the SQLite fixture, on the migrated Postgres (name kept on purpose)."""
    import app.db.models  # noqa: F401

    _empty_tables(pg_engine)
    yield from boot_app(monkeypatch, pg_engine)
