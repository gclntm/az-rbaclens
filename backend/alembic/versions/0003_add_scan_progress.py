"""add progress columns to scans

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("progress_total", sa.Integer(), nullable=True))
    op.add_column(
        "scans",
        sa.Column("progress_done", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("scans", sa.Column("progress_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("scans", "progress_message")
    op.drop_column("scans", "progress_done")
    op.drop_column("scans", "progress_total")
