"""role_assignments: condition / condition_version columns

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-23

ABAC conditions narrow a single role assignment ("may assign any role
except Owner"). They were only inside ``raw``; real columns let the scope
page, the principal page and findings say so. Backfilled from ``raw``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("role_assignments", sa.Column("condition", sa.Text(), nullable=True))
    op.add_column(
        "role_assignments", sa.Column("condition_version", sa.String(length=10), nullable=True)
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE role_assignments SET condition = raw->>'condition', "
            "condition_version = raw->>'condition_version' "
            "WHERE raw ? 'condition' AND coalesce(raw->>'condition', '') <> ''"
        )


def downgrade() -> None:
    op.drop_column("role_assignments", "condition_version")
    op.drop_column("role_assignments", "condition")
