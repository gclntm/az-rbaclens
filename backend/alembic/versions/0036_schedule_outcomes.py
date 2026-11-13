"""Schedules remember how their last job ended

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-10

``job_schedules`` gains:

- ``failures``: failed runs in a row, for the retry delay (15 min, 1 h, 4 h);
- ``settled_job_id``: the last job whose outcome was recorded on the schedule;
- ``covered_until``: Activity Log schedules only, the end of the newest
  window *this schedule* collected. The next run starts there, so a manual
  collection of a short window can no longer hide a gap.

Existing schedules start with their last job as settled and, for
collections, the end of the subscription's newest finished collection:
what the scheduler used until now.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("job_schedules") as t:
        t.add_column(sa.Column("failures", sa.Integer(), nullable=False, server_default="0"))
        t.add_column(sa.Column("settled_job_id", sa.Integer(), nullable=True))
        t.add_column(sa.Column("covered_until", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE job_schedules SET settled_job_id = last_job_id")
    op.execute(
        "UPDATE job_schedules SET covered_until = ("
        "SELECT max(p.end_time) FROM activity_pulls p "
        "WHERE lower(p.subscription_id) = job_schedules.target AND p.status = 'done') "
        "WHERE kind = 'activity_pull'"
    )


def downgrade() -> None:
    with op.batch_alter_table("job_schedules") as t:
        t.drop_column("covered_until")
        t.drop_column("settled_job_id")
        t.drop_column("failures")
