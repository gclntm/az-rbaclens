"""Saved assessment reports

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-09

``assessments`` keeps a report's control statuses and key numbers at one
moment, read-only, so later reports can show what changed since.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_id", sa.Text()),
        sa.Column("scope_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=255)),
        sa.Column("created_by_name", sa.String(length=500)),
        sa.Column("access_as_of", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("statuses", _JSON, nullable=False),
        sa.Column("numbers", _JSON, nullable=False),
    )
    op.create_index("ix_assessments_scope_id", "assessments", ["scope_id"])


def downgrade() -> None:
    op.drop_table("assessments")
