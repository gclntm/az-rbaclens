"""add group_memberships for group-expansion coverage analysis

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-13

Persists transitive membership of every group-typed principal seen
during a scan. Lets the coverage analyzer attribute activity from
group members back to the group's role assignment — Activity Log
records the executing user/SP, not the group it inherited the role
via. Without this, every group RA reads as 0 % coverage.

Scoped to ``scan_id`` so re-running a scan picks up membership
changes; deleting a scan cascades.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "group_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("member_type", sa.String(length=50), nullable=True),
        sa.Column("member_name", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_group_memberships_scan_id",
        "group_memberships",
        ["scan_id"],
    )
    op.create_index(
        "uq_group_memberships_scan_group_member",
        "group_memberships",
        ["scan_id", "group_id", "member_id"],
        unique=True,
    )
    op.create_index(
        "ix_group_memberships_scan_group",
        "group_memberships",
        ["scan_id", "group_id"],
    )
    op.create_index(
        "ix_group_memberships_scan_member",
        "group_memberships",
        ["scan_id", "member_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_group_memberships_scan_member", table_name="group_memberships")
    op.drop_index("ix_group_memberships_scan_group", table_name="group_memberships")
    op.drop_index(
        "uq_group_memberships_scan_group_member",
        table_name="group_memberships",
    )
    op.drop_index("ix_group_memberships_scan_id", table_name="group_memberships")
    op.drop_table("group_memberships")
