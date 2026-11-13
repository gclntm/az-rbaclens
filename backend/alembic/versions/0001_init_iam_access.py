"""init iam_access schema

Revision ID: 0001
Revises:
Create Date: 2026-05-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mgmt_group", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_scans_mgmt_group", "scans", ["mgmt_group"])
    op.create_index("ix_scans_status", "scans", ["status"])

    op.create_table(
        "role_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_level", sa.String(length=20), nullable=False),
        sa.Column("principal_id", sa.String(length=255), nullable=False),
        sa.Column("principal_name", sa.String(length=500), nullable=True),
        sa.Column("principal_type", sa.String(length=50), nullable=False),
        sa.Column("role_name", sa.String(length=255), nullable=True),
        sa.Column("role_definition_id", sa.Text(), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_role_assignments_scan_id", "role_assignments", ["scan_id"])
    op.create_index("ix_role_assignments_scan_scope", "role_assignments", ["scan_id", "scope"])
    op.create_index("ix_role_assignments_scan_role", "role_assignments", ["scan_id", "role_name"])
    op.create_index(
        "ix_role_assignments_scan_principal",
        "role_assignments",
        ["scan_id", "principal_id"],
    )


def downgrade() -> None:
    op.drop_table("role_assignments")
    op.drop_table("scans")
