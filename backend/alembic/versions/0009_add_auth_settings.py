"""add auth_settings (singleton row for Entra ID sign-in config)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-12

Stores the (non-secret) parameters needed to bootstrap MSAL in the SPA
and to validate access tokens on the backend: tenant_id, client_id,
optional comma-separated allowed_group_ids, plus an ``enabled`` flag
that defaults to false so the app keeps working unauthenticated until
the operator explicitly turns auth on.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("tenant_id", sa.String(length=255), nullable=True),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("allowed_group_ids", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("auth_settings")
