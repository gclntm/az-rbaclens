"""add scan_group_resolutions to distinguish empty groups from Graph failures

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-13

An empty security group and a Graph-permission failure both produce
zero rows in ``group_memberships`` — making them indistinguishable
at coverage-render time. This table records every group_id whose
membership we *successfully* fetched (regardless of count), so the
Coverage tab can render "group is empty" vs "membership unresolved"
correctly.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_group_resolutions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_id", sa.String(length=64), nullable=False),
    )
    op.create_index(
        "ix_scan_group_resolutions_scan_id",
        "scan_group_resolutions",
        ["scan_id"],
    )
    op.create_index(
        "uq_scan_group_resolutions_scan_group",
        "scan_group_resolutions",
        ["scan_id", "group_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_scan_group_resolutions_scan_group",
        table_name="scan_group_resolutions",
    )
    op.drop_index(
        "ix_scan_group_resolutions_scan_id",
        table_name="scan_group_resolutions",
    )
    op.drop_table("scan_group_resolutions")
