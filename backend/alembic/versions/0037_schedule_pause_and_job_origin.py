"""Who paused a schedule, and which schedule started a job

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-10

- ``job_schedules.paused_at`` / ``paused_by``: when a schedule was switched
  off and by whom (the signed-in name; empty while sign-in is off). Cleared
  on resume. Schedules already off get no date: it is not known.
- ``scans.origin`` / ``activity_pulls.origin``: ``schedule`` or ``manual``;
  NULL for jobs from before this migration, whose origin was never recorded.
- ``scans.schedule_id`` / ``activity_pulls.schedule_id``: the schedule that
  started the job. Not a foreign key, like ``job_schedules.last_job_id``:
  deleting a schedule keeps its jobs.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("job_schedules") as t:
        t.add_column(sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
        t.add_column(sa.Column("paused_by", sa.String(length=500), nullable=True))
    for table in ("scans", "activity_pulls"):
        with op.batch_alter_table(table) as t:
            t.add_column(sa.Column("origin", sa.String(length=10), nullable=True))
            t.add_column(sa.Column("schedule_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in ("scans", "activity_pulls"):
        with op.batch_alter_table(table) as t:
            t.drop_column("schedule_id")
            t.drop_column("origin")
    with op.batch_alter_table("job_schedules") as t:
        t.drop_column("paused_by")
        t.drop_column("paused_at")
