"""p0: job heartbeat, one in-flight job per target, orphan cleanup

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-22

Three correctness fixes (P0):

1. ``heartbeat_at`` on ``scans`` and ``activity_pulls``. Workers bump it
   on every progress update; the sweeper measures staleness from it
   instead of ``started_at``, so a long-running job that is still
   progressing is never flipped to ``failed`` under a live worker.
2. Partial unique indexes: at most one ``pending`` / ``running`` scan per
   scope and one per subscription for pulls. Two concurrent runs of the
   same target raced on ``group_memberships`` / ``activity_events``
   uniqueness and produced duplicate "latest" candidates.
3. Any row still in-flight when this migration runs is an orphan —
   migrations execute at process start, before any worker thread
   exists — so they are marked ``failed`` first; otherwise the unique
   index creation could fail on pre-existing duplicates.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INFLIGHT = "status IN ('pending','running')"
_ORPHAN_ERROR = (
    "Marked failed on startup: the job was still in-flight when the "
    "backend restarted, so its worker no longer exists."
)


def upgrade() -> None:
    op.add_column("scans", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "activity_pulls",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )

    for table in ("scans", "activity_pulls"):
        op.execute(
            sa.text(
                f"UPDATE {table} SET status = 'failed', error = :err, "
                f"finished_at = now() WHERE {_INFLIGHT}"
            ).bindparams(err=_ORPHAN_ERROR)
        )

    op.create_index(
        "uq_scans_inflight_scope",
        "scans",
        ["scope"],
        unique=True,
        postgresql_where=sa.text(_INFLIGHT),
    )
    op.create_index(
        "uq_activity_pulls_inflight_subscription",
        "activity_pulls",
        ["subscription_id"],
        unique=True,
        postgresql_where=sa.text(_INFLIGHT),
    )


def downgrade() -> None:
    op.drop_index("uq_activity_pulls_inflight_subscription", table_name="activity_pulls")
    op.drop_index("uq_scans_inflight_scope", table_name="scans")
    op.drop_column("activity_pulls", "heartbeat_at")
    op.drop_column("scans", "heartbeat_at")
