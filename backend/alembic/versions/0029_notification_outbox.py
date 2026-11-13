"""Notification outbox (prepared, not connected)

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-08

``notifications`` holds the messages RBAC Lens would send (a question
from a review, a review opened or due soon, a reminder) until email or
Teams is connected. See ``app/core/notify.py``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("recipient_id", sa.String(length=64)),
        sa.Column("recipient_name", sa.String(length=500)),
        sa.Column("subject_ref", sa.String(length=100), nullable=False),
        sa.Column(
            "payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("channel", sa.String(length=20)),
        sa.Column("error", sa.Text()),
        sa.CheckConstraint(
            "kind IN ('ask', 'review_opened', 'review_due', 'reminder')",
            name="ck_notifications_kind",
        ),
    )
    op.create_index("ix_notifications_recipient_id", "notifications", ["recipient_id"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
