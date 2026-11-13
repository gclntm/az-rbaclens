"""Grant origin (created on / by) on role_assignments; operation_names

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-08

Azure records when a role assignment was created and by whom
(``createdOn`` / ``createdBy``). Snapshots kept both inside ``raw``; they
become real columns so every page can say "since 3 Nov 2026, by
sp-terraform". Existing rows are backfilled from ``raw``, and the creator's
name and type from the ``principals`` registry where it already knows the
object (the next snapshot resolves the rest through Graph).

``operation_names`` caches ARM operation display names from Azure's
provider operations metadata, refreshed after Activity Log pulls.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ISO = r"^\d{4}-\d{2}-\d{2}T"


def upgrade() -> None:
    op.add_column(
        "role_assignments", sa.Column("created_on", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("role_assignments", sa.Column("created_by", sa.String(length=255), nullable=True))
    op.add_column(
        "role_assignments", sa.Column("created_by_name", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "role_assignments", sa.Column("created_by_type", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "role_assignments", sa.Column("updated_on", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "operation_names",
        sa.Column("operation", sa.Text(), primary_key=True),
        sa.Column("provider", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("resource_type_display", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operation_names_provider", "operation_names", ["provider"])

    if op.get_bind().dialect.name != "postgresql":
        return
    # Guarded casts: a malformed value stays NULL instead of failing the upgrade.
    op.execute(
        f"""
        UPDATE role_assignments SET
            created_on = CASE WHEN raw->>'created_on' ~ '{_ISO}'
                              THEN (raw->>'created_on')::timestamptz END,
            updated_on = CASE WHEN raw->>'updated_on' ~ '{_ISO}'
                              THEN (raw->>'updated_on')::timestamptz END,
            created_by = NULLIF(lower(raw->>'created_by'), '')
        WHERE raw IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE role_assignments ra SET
            created_by_name = p.display_name,
            created_by_type = p.principal_type
        FROM principals p
        WHERE ra.created_by = p.id
        """
    )


def downgrade() -> None:
    op.drop_table("operation_names")
    op.drop_column("role_assignments", "updated_on")
    op.drop_column("role_assignments", "created_by_type")
    op.drop_column("role_assignments", "created_by_name")
    op.drop_column("role_assignments", "created_by")
    op.drop_column("role_assignments", "created_on")
