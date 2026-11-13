"""Responsible people: responsibilities, tag suggestions, scope tags

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-08

``scopes.tags`` keeps the Azure tags of subscriptions and resource groups
from the newest walk. ``responsibilities`` records who is responsible for
a place (set in RBAC Lens by an admin; it applies to everything inside).
``responsible_suggestions`` lists places whose responsible-person tag
(``owner`` by default, a setting) was read, matched or not.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.add_column("scopes", sa.Column("tags", json_type, nullable=True))
    op.create_table(
        "responsibilities",
        sa.Column(
            "scope_id", sa.Text(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("person_id", sa.String(length=64), nullable=False),
        sa.Column("person_name", sa.String(length=500)),
        sa.Column("person_email", sa.String(length=320)),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("set_by", sa.String(length=255)),
        sa.Column("set_by_name", sa.String(length=500)),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source IN ('manual', 'tag')", name="ck_responsibilities_source"),
    )
    op.create_index("ix_responsibilities_person_id", "responsibilities", ["person_id"])
    op.create_table(
        "responsible_suggestions",
        sa.Column(
            "scope_id", sa.Text(), sa.ForeignKey("scopes.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("tag_value", sa.Text(), nullable=False),
        sa.Column("person_id", sa.String(length=64)),
        sa.Column("person_name", sa.String(length=500)),
        sa.Column("person_email", sa.String(length=320)),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'unmatched', 'ignored', 'used')",
            name="ck_responsible_suggestions_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("responsible_suggestions")
    op.drop_table("responsibilities")
    op.drop_column("scopes", "tags")
