"""PIM: role_eligibilities, assignment type / end on role_assignments,
eligibility status on scans

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-23

Eligible (PIM) assignments never show up in ``role_assignments`` — the
principal holds nothing until activation — so snapshots record them
separately. Active assignments gain ``assignment_type`` (Assigned /
Activated) and ``end_at`` (NULL = permanent). ``scans.eligibility_status``
says whether a snapshot read PIM at all; existing snapshots stay NULL
("not checked").
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("eligibility_status", sa.String(length=20), nullable=True))
    op.add_column("scans", sa.Column("eligibility_error", sa.Text(), nullable=True))
    op.add_column(
        "role_assignments", sa.Column("assignment_type", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "role_assignments", sa.Column("end_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "role_eligibilities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("azure_id", sa.String(length=255), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_name", sa.Text(), nullable=True),
        sa.Column("scope_level", sa.String(length=20), nullable=False),
        sa.Column("principal_id", sa.String(length=255), nullable=False),
        sa.Column("principal_name", sa.String(length=500), nullable=True),
        sa.Column("principal_type", sa.String(length=50), nullable=False),
        sa.Column("role_definition_id", sa.Text(), nullable=False),
        sa.Column("role_name", sa.String(length=255), nullable=True),
        sa.Column("role_type", sa.String(length=20), nullable=True),
        sa.Column("member_type", sa.String(length=20), nullable=True),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column("condition_version", sa.String(length=10), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
    )
    op.create_index("ix_role_eligibilities_scan_id", "role_eligibilities", ["scan_id"])
    op.create_index(
        "uq_role_eligibilities_scan_azure",
        "role_eligibilities",
        ["scan_id", "azure_id"],
        unique=True,
    )
    op.create_index("ix_role_eligibilities_scan_scope", "role_eligibilities", ["scan_id", "scope"])
    op.create_index(
        "ix_role_eligibilities_scan_principal", "role_eligibilities", ["scan_id", "principal_id"]
    )


def downgrade() -> None:
    op.drop_table("role_eligibilities")
    op.drop_column("role_assignments", "end_at")
    op.drop_column("role_assignments", "assignment_type")
    op.drop_column("scans", "eligibility_error")
    op.drop_column("scans", "eligibility_status")
