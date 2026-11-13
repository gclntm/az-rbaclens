"""Index access_changes.activity_event_id

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-10

``access_changes.activity_event_id`` points at ``activity_events`` with
``ON DELETE SET NULL``. Without an index, every Activity Log row deleted
(compaction folding a day, retention) made Postgres read the whole
``access_changes`` table to find rows pointing at it: about two million full
reads on a tenant whose log was compacted once.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_access_changes_activity_event_id", "access_changes", ["activity_event_id"])


def downgrade() -> None:
    op.drop_index("ix_access_changes_activity_event_id", table_name="access_changes")
