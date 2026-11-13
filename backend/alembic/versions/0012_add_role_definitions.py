"""add role_definitions cache table

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-13

One row per distinct Azure role definition GUID. Populated lazily
during the scan walk — we already call ``role_definitions.get_by_id``
to resolve the role name, this just keeps the rest of the response
(description, permissions, assignableScopes) instead of discarding it.
Shared across scans and survives scan deletion since the role
catalogue is global per definition.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "role_definitions",
        sa.Column("role_definition_id", sa.String(length=64), primary_key=True),
        sa.Column("role_name", sa.String(length=255), nullable=True),
        sa.Column("role_type", sa.String(length=20), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "permissions",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "assignable_scopes",
            sa.JSON().with_variant(JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("role_definitions")
