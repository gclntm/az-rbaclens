"""Every migration runs on Postgres, and the schema they build is the models' schema."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from tests.pg.conftest import alembic_config


def test_migrations_reach_head(pg_engine):
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    with pg_engine.connect() as conn:
        assert MigrationContext.configure(conn).get_current_revision() == head


def test_migrated_schema_matches_the_models(pg_engine):
    """A model changed without a migration (or the reverse) shows up here."""
    import app.db.models  # noqa: F401
    from app.db.base import Base

    with pg_engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diffs = compare_metadata(ctx, Base.metadata)
    assert diffs == [], "\n".join(map(str, diffs))
