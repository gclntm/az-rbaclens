"""Owners and secrets in the monthly review

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-18

A review item, and the to-do it leads to, can now be an owner of an app or
group, or an app's secret, besides a grant: ``kind`` (grant | owner |
secret), ``subject_key`` (the grant id, ``owner:<object>:<owner>`` or
``secret:<object>:<via>:<key id>``) and ``detail``. Existing rows are
grants: their subject is their grant id.

``review_decisions.review_item_id``: the review item whose "keep" marked the
finding reviewed, so undoing that keep reopens it. Existing marks noted
"Kept in the monthly review" are linked to the kept item of their grant
when one is found.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    for table in ("review_items", "todos"):
        with op.batch_alter_table(table) as t:
            t.add_column(
                sa.Column("kind", sa.String(length=10), nullable=False, server_default="grant")
            )
            t.add_column(sa.Column("subject_key", sa.String(length=200)))
            t.add_column(sa.Column("detail", _JSON))
        op.execute(f"UPDATE {table} SET subject_key = grant_fingerprint")
        with op.batch_alter_table(table) as t:
            t.alter_column("subject_key", existing_type=sa.String(length=200), nullable=False)
    op.drop_index("uq_review_items_round_grant", table_name="review_items")
    op.create_index(
        "uq_review_items_round_subject", "review_items", ["round_id", "subject_key"], unique=True
    )
    with op.batch_alter_table("review_decisions") as t:
        t.add_column(sa.Column("review_item_id", sa.Integer()))
        t.create_foreign_key(
            "fk_review_decisions_review_item_id",
            "review_items",
            ["review_item_id"],
            ["id"],
            ondelete="SET NULL",
        )
    _link_existing_marks()


def _link_existing_marks() -> None:
    """Link marks a review's "keep" made before 0045 to that kept item (newest first)."""
    from app.core.findings import grant_fingerprint

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT d.fingerprint, ra.scope, ra.role_definition_id, ra.principal_id "
            "FROM review_decisions d "
            "JOIN findings f ON f.fingerprint = d.fingerprint "
            "JOIN role_assignments ra ON ra.id = f.role_assignment_id "
            "WHERE d.note = 'Kept in the monthly review' AND d.review_item_id IS NULL"
        )
    ).all()
    seen: set[str] = set()
    for fingerprint, scope, role, principal in rows:
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        item = bind.execute(
            sa.text(
                "SELECT id FROM review_items WHERE grant_fingerprint = :g AND kind = 'grant' "
                "AND decision = 'keep' ORDER BY id DESC LIMIT 1"
            ),
            {"g": grant_fingerprint(scope, role, principal)},
        ).scalar()
        if item is not None:
            bind.execute(
                sa.text("UPDATE review_decisions SET review_item_id = :i WHERE fingerprint = :f"),
                {"i": item, "f": fingerprint},
            )


def downgrade() -> None:
    with op.batch_alter_table("review_decisions") as t:
        t.drop_constraint("fk_review_decisions_review_item_id", type_="foreignkey")
        t.drop_column("review_item_id")
    op.execute("DELETE FROM review_items WHERE kind <> 'grant'")
    op.drop_index("uq_review_items_round_subject", table_name="review_items")
    op.create_index(
        "uq_review_items_round_grant",
        "review_items",
        ["round_id", "grant_fingerprint"],
        unique=True,
    )
    for table in ("todos", "review_items"):
        with op.batch_alter_table(table) as t:
            t.drop_column("detail")
            t.drop_column("subject_key")
            t.drop_column("kind")
