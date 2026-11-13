"""Changes feed (access_changes), daily-aligned schedules, app_settings

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-08

``access_changes`` records who got or lost access, when, and who did it;
``app/core/changes.py`` fills it after each check and once for the
history already stored. ``job_schedules.at_hour`` aligns a daily
schedule to an hour of container local time (the nightly whole-tenant
check). ``app_settings`` keeps small app-wide state, such as whether the
default schedules were created.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job_schedules", sa.Column("at_hour", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_job_schedules_at_hour",
        "job_schedules",
        "at_hour IS NULL OR (at_hour BETWEEN 0 AND 23 AND every_hours = 24)",
    )

    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", json_type, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "access_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "first_seen_scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="SET NULL")
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_name", sa.Text()),
        sa.Column("scope_level", sa.String(length=20), nullable=False),
        sa.Column("principal_id", sa.String(length=255), nullable=False),
        sa.Column("principal_name", sa.String(length=500)),
        sa.Column("principal_type", sa.String(length=50)),
        sa.Column("role_definition_id", sa.Text()),
        sa.Column("role_name", sa.String(length=255)),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("via_group_id", sa.String(length=255)),
        sa.Column("via_group_name", sa.String(length=500)),
        sa.Column("actor_id", sa.String(length=255)),
        sa.Column("actor_name", sa.String(length=500)),
        sa.Column("azure_id", sa.Text()),
        sa.Column(
            "activity_event_id",
            sa.Integer(),
            sa.ForeignKey("activity_events.id", ondelete="SET NULL"),
        ),
        sa.Column("subject_key", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('granted', 'removed', 'eligible_added', 'eligible_removed', "
            "'member_added', 'member_removed')",
            name="ck_access_changes_kind",
        ),
        sa.CheckConstraint(
            "source IN ('activity_log', 'created_on', 'daily_check')",
            name="ck_access_changes_source",
        ),
    )
    op.create_index("ix_access_changes_occurred_at", "access_changes", ["occurred_at"])
    op.create_index("ix_access_changes_principal_id", "access_changes", ["principal_id"])
    op.create_index(
        "ix_access_changes_first_seen_scan_id", "access_changes", ["first_seen_scan_id"]
    )
    op.create_index(
        "uq_access_changes_subject",
        "access_changes",
        ["subject_key", "kind", "occurred_at"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("access_changes")
    op.drop_table("app_settings")
    op.drop_constraint("ck_job_schedules_at_hour", "job_schedules", type_="check")
    op.drop_column("job_schedules", "at_hour")
