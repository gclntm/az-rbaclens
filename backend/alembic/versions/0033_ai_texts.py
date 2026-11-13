"""Cached AI wordings

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-09

``ai_texts`` caches the plain-language wording Claude writes for an advice
(optional, off by default; core/ai_wording.py), keyed by a hash of the
model, the prompt version and the facts sent.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_texts",
        sa.Column("hash", sa.String(length=64), primary_key=True),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_texts")
