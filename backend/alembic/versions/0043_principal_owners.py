"""Owners of the apps and groups that hold access

Revision ID: 0043
Revises: 0042
Create Date: 2026-11-18

An app's owner can add a secret to it and act as it; a group's owner
decides its members. Each check now reads them (``tools/graph_owners.py``):
``principal_owners`` holds one row per owner, ``scan_owner_resolutions`` one
per app or group whose owners Graph answered for (none: unknown). An owner
added or removed is a change on the Changes page (``owner_added`` /
``owner_removed``).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "principal_owners",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("object_kind", sa.String(length=10), nullable=False),
        sa.Column("via", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("owner_type", sa.String(length=50)),
        sa.Column("owner_name", sa.String(length=500)),
    )
    op.create_index("ix_principal_owners_scan_id", "principal_owners", ["scan_id"])
    op.create_index("ix_principal_owners_owner_id", "principal_owners", ["owner_id"])
    op.create_index(
        "uq_principal_owners_scan_object_via_owner",
        "principal_owners",
        ["scan_id", "object_id", "via", "owner_id"],
        unique=True,
    )
    op.create_table(
        "scan_owner_resolutions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("registered_elsewhere", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("app_id", sa.String(length=64)),
    )
    op.create_index("ix_scan_owner_resolutions_scan_id", "scan_owner_resolutions", ["scan_id"])
    op.create_index(
        "uq_scan_owner_resolutions_scan_object",
        "scan_owner_resolutions",
        ["scan_id", "object_id"],
        unique=True,
    )
    _kinds(("'owner_added'", "'owner_removed'"))


_KINDS = (
    "'granted'",
    "'removed'",
    "'eligible_added'",
    "'eligible_removed'",
    "'member_added'",
    "'member_removed'",
)


def _kinds(extra: tuple[str, ...]) -> None:
    with op.batch_alter_table("access_changes") as t:
        t.drop_constraint("ck_access_changes_kind", type_="check")
        t.create_check_constraint(
            "ck_access_changes_kind", f"kind IN ({', '.join(_KINDS + extra)})"
        )


def downgrade() -> None:
    op.execute("DELETE FROM access_changes WHERE kind IN ('owner_added', 'owner_removed')")
    _kinds(())
    op.drop_table("scan_owner_resolutions")
    op.drop_table("principal_owners")
