"""add role_type to role_assignments

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-11

``role_type`` is Azure's own classification (``BuiltInRole`` or
``CustomRole``), captured during phase 2 via
``auth_client.role_definitions.get_by_id``. Lets the UI surface custom
roles (which typically deserve more scrutiny in an access review).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "role_assignments",
        sa.Column("role_type", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("role_assignments", "role_type")
