"""Notifications: data_stale

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-09

``data_stale`` tells the admins, once a day, that the data stopped being kept
up to date: a schedule off, the check failed or overdue, or the Activity Log
behind (core/staleness.py).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "kind IN ('ask', 'review_opened', 'review_due', 'reminder', 'todo_overdue')"
_AFTER = "kind IN ('ask', 'review_opened', 'review_due', 'reminder', 'todo_overdue', 'data_stale')"


def upgrade() -> None:
    with op.batch_alter_table("notifications") as t:
        t.drop_constraint("ck_notifications_kind", type_="check")
        t.create_check_constraint("ck_notifications_kind", _AFTER)


def downgrade() -> None:
    op.execute("DELETE FROM notifications WHERE kind = 'data_stale'")
    with op.batch_alter_table("notifications") as t:
        t.drop_constraint("ck_notifications_kind", type_="check")
        t.create_check_constraint("ck_notifications_kind", _BEFORE)
