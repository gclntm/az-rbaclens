"""add scope_name to role_assignments

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "role_assignments",
        sa.Column("scope_name", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("role_assignments", "scope_name")
