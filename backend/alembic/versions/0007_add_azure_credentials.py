"""add azure_credentials table

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-09

Singleton row (id=1) for Azure SP credentials entered via the settings
page. ``client_secret_encrypted`` is encrypted with Fernet using the
ENCRYPTION_KEY env var. ``tenant_id`` / ``client_id`` are stored plain
(non-secret public identifiers).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "azure_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=True),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("client_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("azure_credentials")
