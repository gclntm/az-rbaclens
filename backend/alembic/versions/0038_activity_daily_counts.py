"""Activity Log: machine activity as daily counts

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-10

- ``activity_events.event_count``: how many events a row stands for; 1 for an
  event kept in full. ``first_occurred_at``: on a daily row, the first of
  them (``occurred_at`` is the last); NULL on an event kept in full.
- ``activity_compacted_days``: the days of a subscription (or of the
  management groups) whose machine activity has been folded into daily rows
  (core/activity_compaction.py). A collection skips those events on such a
  day: they are counted already.

The stored events are folded by the retention loop, not here: on a large
database that takes minutes, which must not hold up the app's start.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("activity_events") as t:
        t.add_column(sa.Column("event_count", sa.Integer(), nullable=False, server_default="1"))
        t.add_column(sa.Column("first_occurred_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "activity_compacted_days",
        sa.Column("subscription_id", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("compacted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("events_before", sa.Integer(), nullable=False),
        sa.Column("rows_after", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("subscription_id", "day"),
    )


def downgrade() -> None:
    op.drop_table("activity_compacted_days")
    with op.batch_alter_table("activity_events") as t:
        t.drop_column("first_occurred_at")
        t.drop_column("event_count")
