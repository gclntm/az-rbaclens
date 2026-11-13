"""Monthly reviews: review_rounds and review_items

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-08

A round is one month's review of one place by its responsible person
(``scope_id`` NULL: the admins' round). Items are the grants in it, with
Keep / Remove / Ask. See ``app/core/reviews.py``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_rounds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_id", sa.Text(), sa.ForeignKey("scopes.id", ondelete="CASCADE")),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("reviewer_id", sa.String(length=64)),
        sa.Column("reviewer_name", sa.String(length=500)),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("finished_by_name", sa.String(length=500)),
    )
    op.create_index("ix_review_rounds_scope_id", "review_rounds", ["scope_id"])
    op.create_index("ix_review_rounds_reviewer_id", "review_rounds", ["reviewer_id"])
    op.create_index(
        "uq_review_rounds_scope_period", "review_rounds", ["scope_id", "period"], unique=True
    )
    op.create_index(
        "uq_review_rounds_admins_period",
        "review_rounds",
        ["period"],
        unique=True,
        postgresql_where=sa.text("scope_id IS NULL"),
        sqlite_where=sa.text("scope_id IS NULL"),
    )
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "round_id",
            sa.Integer(),
            sa.ForeignKey("review_rounds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("grant_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_name", sa.Text()),
        sa.Column("scope_level", sa.String(length=20), nullable=False),
        sa.Column("principal_id", sa.String(length=255), nullable=False),
        sa.Column("principal_name", sa.String(length=500)),
        sa.Column("principal_type", sa.String(length=50)),
        sa.Column("role_definition_id", sa.Text(), nullable=False),
        sa.Column("role_name", sa.String(length=255)),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("azure_id", sa.Text()),
        sa.Column("moved_from_scope", sa.Text()),
        sa.Column("decision", sa.String(length=10)),
        sa.Column("note", sa.Text()),
        sa.Column("decided_by", sa.String(length=255)),
        sa.Column("decided_by_name", sa.String(length=500)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("carried_from_round_id", sa.Integer()),
        sa.Column("ask_remind_at", sa.DateTime(timezone=True)),
        sa.Column("gone_at", sa.DateTime(timezone=True)),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('keep', 'remove', 'ask')",
            name="ck_review_items_decision",
        ),
    )
    op.create_index("ix_review_items_round_id", "review_items", ["round_id"])
    op.create_index("ix_review_items_grant_fingerprint", "review_items", ["grant_fingerprint"])
    op.create_index(
        "uq_review_items_round_grant",
        "review_items",
        ["round_id", "grant_fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("review_items")
    op.drop_table("review_rounds")
