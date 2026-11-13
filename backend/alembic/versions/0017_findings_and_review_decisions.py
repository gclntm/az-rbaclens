"""findings, review_decisions, scan_coverage cache

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-22

Phase 1 of docs/redesign-plan.md: the coverage analysis moves from
request time to snapshot end. ``scan_coverage`` caches the full
``RoleCoverageResult`` per scan so the Coverage tab is a read;
``findings`` is the derived review worklist (replaced per recompute);
``review_decisions`` holds reviewer verdicts keyed by a fingerprint
that is stable across scans.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_coverage",
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "previous_scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )

    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_assignment_id",
            sa.Integer(),
            sa.ForeignKey("role_assignments.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_findings_scan_id", "findings", ["scan_id"])
    op.create_index("ix_findings_role_assignment_id", "findings", ["role_assignment_id"])
    op.create_index("ix_findings_fingerprint", "findings", ["fingerprint"])
    op.create_index("ix_findings_scan_severity", "findings", ["scan_id", "severity"])
    op.create_index(
        "uq_findings_scan_fingerprint", "findings", ["scan_id", "fingerprint"], unique=True
    )

    op.create_table(
        "review_decisions",
        sa.Column("fingerprint", sa.String(length=64), primary_key=True),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("actor_name", sa.String(length=500), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("review_decisions")
    for name in (
        "uq_findings_scan_fingerprint",
        "ix_findings_scan_severity",
        "ix_findings_fingerprint",
        "ix_findings_role_assignment_id",
        "ix_findings_scan_id",
    ):
        op.drop_index(name, table_name="findings")
    op.drop_table("findings")
    op.drop_table("scan_coverage")
