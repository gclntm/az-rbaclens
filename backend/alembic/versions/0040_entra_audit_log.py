"""Group membership events from the Entra ID audit log

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-18

``directory_audit_events`` keeps who added or removed a member of a group,
read from the Entra ID audit log (``tools/entra_audit.py``). The changes
feed uses them to say who changed a group that holds access, and when:
``access_changes.source`` gains ``entra_audit``, with the kind of author
(``actor_type``) and the nested group the member was added to or removed
from (``direct_group_name``).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_UTC = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "directory_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("audit_id", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("occurred_at", _UTC, nullable=False),
        sa.Column("result", sa.String(length=20)),
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("group_name", sa.String(length=500)),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("member_type", sa.String(length=50)),
        sa.Column("member_name", sa.String(length=500)),
        sa.Column("initiator_type", sa.String(length=10)),
        sa.Column("initiator_id", sa.String(length=64)),
        sa.Column("initiator_name", sa.String(length=500)),
        sa.Column("raw", _JSON),
        sa.Column("created_at", _UTC, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("audit_id", name="uq_directory_audit_events_audit_id"),
    )
    op.create_index(
        "ix_directory_audit_events_occurred_at", "directory_audit_events", ["occurred_at"]
    )
    op.create_index("ix_directory_audit_events_member_id", "directory_audit_events", ["member_id"])

    with op.batch_alter_table("access_changes") as t:
        t.add_column(sa.Column("actor_type", sa.String(length=10)))
        t.add_column(sa.Column("direct_group_name", sa.String(length=500)))
        t.drop_constraint("ck_access_changes_source", type_="check")
        t.create_check_constraint(
            "ck_access_changes_source",
            "source IN ('activity_log', 'created_on', 'daily_check', 'entra_audit')",
        )


def downgrade() -> None:
    op.execute("UPDATE access_changes SET source = 'daily_check' WHERE source = 'entra_audit'")
    with op.batch_alter_table("access_changes") as t:
        t.drop_constraint("ck_access_changes_source", type_="check")
        t.create_check_constraint(
            "ck_access_changes_source",
            "source IN ('activity_log', 'created_on', 'daily_check')",
        )
        t.drop_column("direct_group_name")
        t.drop_column("actor_type")
    op.drop_index("ix_directory_audit_events_member_id", table_name="directory_audit_events")
    op.drop_index("ix_directory_audit_events_occurred_at", table_name="directory_audit_events")
    op.drop_table("directory_audit_events")
