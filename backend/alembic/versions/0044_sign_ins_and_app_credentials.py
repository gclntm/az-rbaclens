"""Last sign-ins of people with powerful grants, and app secrets

Revision ID: 0044
Revises: 0043
Create Date: 2026-11-18

Each check reads, through Microsoft Graph, when the people holding a grant
that can change who has access or get keys last signed in
(``principal_sign_ins``), and the secrets and certificates of the apps
holding a grant (``app_credentials``: kind and dates only).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UTC = sa.DateTime(timezone=True)


def _scan_fk() -> sa.Column:
    return sa.Column(
        "scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "principal_sign_ins",
        sa.Column("id", sa.Integer(), primary_key=True),
        _scan_fk(),
        sa.Column("principal_id", sa.String(length=64), nullable=False),
        sa.Column("last_sign_in_at", _UTC),
        sa.Column("last_non_interactive_at", _UTC),
        sa.Column("last_successful_at", _UTC),
        sa.Column("account_created_at", _UTC),
    )
    op.create_index("ix_principal_sign_ins_scan_id", "principal_sign_ins", ["scan_id"])
    op.create_index(
        "uq_principal_sign_ins_scan_principal",
        "principal_sign_ins",
        ["scan_id", "principal_id"],
        unique=True,
    )
    op.create_table(
        "app_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        _scan_fk(),
        sa.Column("object_id", sa.String(length=64), nullable=False),
        sa.Column("via", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("start_at", _UTC),
        sa.Column("end_at", _UTC),
    )
    op.create_index("ix_app_credentials_scan_id", "app_credentials", ["scan_id"])
    op.create_index(
        "uq_app_credentials_scan_object_key",
        "app_credentials",
        ["scan_id", "object_id", "via", "key_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("app_credentials")
    op.drop_table("principal_sign_ins")
