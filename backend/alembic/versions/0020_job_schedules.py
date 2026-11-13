"""job_schedules: recurring snapshots and activity pulls

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-22

Phase 3 of docs/redesign-plan.md. The scheduler loop in
``app.core.scheduler`` starts due schedules as ordinary ``scans`` /
``activity_pulls`` rows, replacing the manual pull form.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("every_hours", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("initial_window_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_job_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('snapshot', 'activity_pull')", name="ck_job_schedules_kind"),
        sa.CheckConstraint("every_hours BETWEEN 1 AND 720", name="ck_job_schedules_every"),
    )
    op.create_index(
        "uq_job_schedules_kind_target", "job_schedules", ["kind", "target"], unique=True
    )
    op.create_index("ix_job_schedules_due", "job_schedules", ["enabled", "next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_job_schedules_due", table_name="job_schedules")
    op.drop_index("uq_job_schedules_kind_target", table_name="job_schedules")
    op.drop_table("job_schedules")
