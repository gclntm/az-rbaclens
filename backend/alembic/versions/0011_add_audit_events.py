"""add audit_events for mutation tracking

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-12

Captures one row per mutating /api/* call (POST/PUT/PATCH/DELETE):
when it happened, which principal (or "anonymous" with auth off),
the HTTP method + path, and the response status. Body content is
NOT logged — would leak secrets on the credentials endpoint.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("actor_name", sa.String(length=500), nullable=True),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")
