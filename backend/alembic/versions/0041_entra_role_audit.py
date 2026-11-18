"""Entra role entries in the audit log table

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-18

``directory_audit_events`` also keeps the Entra ID audit log's role entries
(tools/entra_audit.py): ``kind`` says what the entry is about (``group``
membership, a ``role``, an ``eligible`` role through PIM, or a PIM
``activation``), ``role_id`` names the role. ``group_id`` becomes optional:
a role entry has none.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("directory_audit_events") as t:
        t.add_column(
            sa.Column("kind", sa.String(length=12), nullable=False, server_default="group")
        )
        t.add_column(sa.Column("role_id", sa.String(length=64)))
        t.alter_column("group_id", existing_type=sa.String(length=64), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM directory_audit_events WHERE kind <> 'group'")
    with op.batch_alter_table("directory_audit_events") as t:
        t.alter_column("group_id", existing_type=sa.String(length=64), nullable=False)
        t.drop_column("role_id")
        t.drop_column("kind")
