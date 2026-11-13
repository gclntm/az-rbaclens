"""add admin_group_ids to auth_settings

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-12

When set, restricts admin endpoints (``/api/settings/*``,
``/api/settings/database/*``) to users whose access-token ``groups``
claim contains one of these Entra group object IDs. Empty (default)
preserves the previous behaviour: every authenticated user is admin.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_settings",
        sa.Column("admin_group_ids", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_settings", "admin_group_ids")
