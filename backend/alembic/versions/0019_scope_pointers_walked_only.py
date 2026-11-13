"""scope pointers: only nodes a scan walked may claim it

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-22

0018 let every node in a scan's hierarchy claim the scan as its
``latest_scan_id``, including *ancestors* registered only so inherited
RAs had somewhere to hang (e.g. Tenant Root Group in a subscription
scan). Those MGs then reported a subscription scan's numbers.

Recompute: clear all pointers, then for every completed scan walk its
hierarchy downward from the root (child → parent links) and let the
newest scan that walked a node own it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE scopes SET latest_scan_id = NULL"))
    op.execute(
        sa.text(
            """
            WITH RECURSIVE nodes AS (
                SELECT s.id AS scan_id, h.key AS id, h.value->>'parent' AS parent
                  FROM scans s, jsonb_each(s.hierarchy) h
                 WHERE s.status = 'done' AND s.hierarchy IS NOT NULL
                   AND jsonb_typeof(s.hierarchy) = 'object'
            ),
            walked AS (
                SELECT s.id AS scan_id, LOWER(s.scope) AS id
                  FROM scans s WHERE s.status = 'done'
                UNION
                SELECT n.scan_id, n.id
                  FROM nodes n JOIN walked w ON n.scan_id = w.scan_id AND n.parent = w.id
            ),
            best AS (SELECT id, MAX(scan_id) AS scan_id FROM walked GROUP BY id)
            UPDATE scopes sc SET latest_scan_id = b.scan_id
              FROM best b WHERE sc.id = b.id
            """
        )
    )


def downgrade() -> None:
    # Pointer semantics only; nothing structural to undo. Re-running the
    # 0018 rule would reintroduce the bug, so leave pointers as they are.
    pass
