"""Advice in reviews, the Change decision, and to-dos

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-08

``review_items`` gains the playbook's suggested decision (``suggested``,
``suggested_rule``, ``suggested_title``) and accepts ``change``.
``todos`` holds the changes to make in Azure after a finished review; the
daily check closes them. Notifications gain ``todo_overdue``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("review_items") as t:
        t.add_column(sa.Column("suggested", sa.String(length=10)))
        t.add_column(sa.Column("suggested_rule", sa.String(length=40)))
        t.add_column(sa.Column("suggested_title", sa.Text()))
        t.drop_constraint("ck_review_items_decision", type_="check")
        t.create_check_constraint(
            "ck_review_items_decision",
            "decision IS NULL OR decision IN ('keep', 'remove', 'change', 'ask')",
        )

    with op.batch_alter_table("notifications") as t:
        t.drop_constraint("ck_notifications_kind", type_="check")
        t.create_check_constraint(
            "ck_notifications_kind",
            "kind IN ('ask', 'review_opened', 'review_due', 'reminder', 'todo_overdue')",
        )

    op.create_table(
        "todos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "round_id",
            sa.Integer(),
            sa.ForeignKey("review_rounds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            sa.Integer(),
            sa.ForeignKey("review_items.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("grant_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_name", sa.Text()),
        sa.Column("principal_id", sa.String(length=255), nullable=False),
        sa.Column("principal_name", sa.String(length=500)),
        sa.Column("principal_type", sa.String(length=50)),
        sa.Column("role_name", sa.String(length=255)),
        sa.Column("note", sa.Text()),
        sa.Column("advice_rule", sa.String(length=40)),
        sa.Column("how", _JSON, nullable=False),
        sa.Column("commands", _JSON, nullable=False),
        sa.Column("reviewer_id", sa.String(length=255)),
        sa.Column("reviewer_name", sa.String(length=500)),
        sa.Column("decided_by_name", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="open"),
        sa.Column("done_at", sa.DateTime(timezone=True)),
        sa.Column("dismissed_reason", sa.Text()),
        sa.Column("dismissed_by_name", sa.String(length=500)),
        sa.CheckConstraint("action IN ('remove', 'change')", name="ck_todos_action"),
        sa.CheckConstraint("status IN ('open', 'done', 'dismissed')", name="ck_todos_status"),
    )
    op.create_index("ix_todos_round_id", "todos", ["round_id"])
    op.create_index("ix_todos_grant_fingerprint", "todos", ["grant_fingerprint"])
    op.create_index("ix_todos_reviewer_id", "todos", ["reviewer_id"])
    op.create_index("ix_todos_status", "todos", ["status"])


def downgrade() -> None:
    op.drop_table("todos")
    op.execute("DELETE FROM notifications WHERE kind = 'todo_overdue'")
    with op.batch_alter_table("notifications") as t:
        t.drop_constraint("ck_notifications_kind", type_="check")
        t.create_check_constraint(
            "ck_notifications_kind", "kind IN ('ask', 'review_opened', 'review_due', 'reminder')"
        )
    op.execute("UPDATE review_items SET decision = NULL WHERE decision = 'change'")
    with op.batch_alter_table("review_items") as t:
        t.drop_constraint("ck_review_items_decision", type_="check")
        t.create_check_constraint(
            "ck_review_items_decision", "decision IS NULL OR decision IN ('keep', 'remove', 'ask')"
        )
        t.drop_column("suggested_title")
        t.drop_column("suggested_rule")
        t.drop_column("suggested")
