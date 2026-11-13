"""add activity_pulls + activity_events for Azure Monitor data

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-13

Phase 1 of the events feature: storage for Azure Activity Log
entries pulled per subscription. Events are deduped by
(subscription_id, event_data_id) so overlapping pulls are
idempotent. Pulls are kept as metadata (when/window/status) but
don't FK to events — events outlive the pull that first fetched
them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_pulls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subscription_id", sa.String(length=64), nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("events_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_persisted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_activity_pulls_subscription_id",
        "activity_pulls",
        ["subscription_id"],
    )
    op.create_index("ix_activity_pulls_status", "activity_pulls", ["status"])

    op.create_table(
        "activity_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subscription_id", sa.String(length=64), nullable=False),
        sa.Column("event_data_id", sa.String(length=64), nullable=False),
        sa.Column("caller", sa.String(length=500), nullable=True),
        sa.Column("caller_type", sa.String(length=50), nullable=True),
        sa.Column("operation_name", sa.String(length=500), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("resource_type", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "raw",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_activity_events_sub_event",
        "activity_events",
        ["subscription_id", "event_data_id"],
        unique=True,
    )
    op.create_index(
        "ix_activity_events_sub_time",
        "activity_events",
        ["subscription_id", "occurred_at"],
    )
    op.create_index(
        "ix_activity_events_caller_time",
        "activity_events",
        ["caller", "occurred_at"],
    )
    op.create_index("ix_activity_events_caller", "activity_events", ["caller"])
    op.create_index(
        "ix_activity_events_operation_name",
        "activity_events",
        ["operation_name"],
    )
    op.create_index("ix_activity_events_status", "activity_events", ["status"])
    op.create_index(
        "ix_activity_events_occurred_at",
        "activity_events",
        ["occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_events_occurred_at", table_name="activity_events")
    op.drop_index("ix_activity_events_status", table_name="activity_events")
    op.drop_index("ix_activity_events_operation_name", table_name="activity_events")
    op.drop_index("ix_activity_events_caller", table_name="activity_events")
    op.drop_index("ix_activity_events_caller_time", table_name="activity_events")
    op.drop_index("ix_activity_events_sub_time", table_name="activity_events")
    op.drop_index("uq_activity_events_sub_event", table_name="activity_events")
    op.drop_table("activity_events")
    op.drop_index("ix_activity_pulls_status", table_name="activity_pulls")
    op.drop_index("ix_activity_pulls_subscription_id", table_name="activity_pulls")
    op.drop_table("activity_pulls")
