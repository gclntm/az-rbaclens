"""scopes and principals registries, backfilled from completed scans

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-22

Redesign, phase 2. ``scopes`` makes "the current state of
prod" one row (``latest_scan_id``) instead of a ``max(created_at)``
dance plus JSONB parsing; ``principals`` gives name-first tables and an
indexed principal lookup instead of ``lower(principal_id)`` seq scans.

Backfill is plain SQL over existing ``done`` scans: the newest scan that
walked a scope wins its row; a principal keeps the newest non-null name
any scan resolved for it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scopes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column(
            "parent_id", sa.Text(), sa.ForeignKey("scopes.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("resource_type", sa.String(length=255), nullable=True),
        sa.Column(
            "latest_scan_id",
            sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scopes_level", "scopes", ["level"])
    op.create_index("ix_scopes_parent_id", "scopes", ["parent_id"])
    op.create_index("ix_scopes_latest_scan_id", "scopes", ["latest_scan_id"])

    op.create_table(
        "principals",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.String(length=500), nullable=True),
        sa.Column("principal_type", sa.String(length=50), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_principals_principal_type", "principals", ["principal_type"])

    # ---- backfill scopes from scans.hierarchy ------------------------------------
    op.execute(
        sa.text(
            """
            WITH nodes AS (
                SELECT h.key                AS id,
                       h.value->>'name'     AS name,
                       h.value->>'level'    AS level,
                       h.value->>'parent'   AS parent,
                       h.value->>'type'     AS resource_type,
                       s.id                 AS scan_id,
                       s.created_at         AS created_at,
                       COALESCE(s.finished_at, s.created_at) AS seen_at
                FROM scans s, jsonb_each(s.hierarchy) h
                WHERE s.status = 'done' AND s.hierarchy IS NOT NULL
                  AND jsonb_typeof(s.hierarchy) = 'object'
            ),
            latest AS (
                SELECT DISTINCT ON (id) * FROM nodes ORDER BY id, scan_id DESC
            ),
            firsts AS (
                SELECT id, MIN(created_at) AS first_seen FROM nodes GROUP BY id
            )
            INSERT INTO scopes (id, name, level, resource_type, latest_scan_id,
                                first_seen_at, last_seen_at)
            SELECT l.id, l.name, COALESCE(l.level, 'resource'), l.resource_type,
                   l.scan_id, f.first_seen, l.seen_at
            FROM latest l JOIN firsts f USING (id)
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH nodes AS (
                SELECT h.key AS id, h.value->>'parent' AS parent, s.id AS scan_id
                FROM scans s, jsonb_each(s.hierarchy) h
                WHERE s.status = 'done' AND s.hierarchy IS NOT NULL
                  AND jsonb_typeof(s.hierarchy) = 'object'
            ),
            -- Newest scan that actually KNOWS a parent wins: a scan rooted
            -- at a subscription records no parent for its root, but an
            -- older MG-rooted scan does, and that link is still true.
            latest AS (
                SELECT DISTINCT ON (id) * FROM nodes
                ORDER BY id, (parent IS NULL), scan_id DESC
            )
            UPDATE scopes sc
               SET parent_id = l.parent
              FROM latest l
             WHERE sc.id = l.id AND l.parent IS NOT NULL
               AND EXISTS (SELECT 1 FROM scopes p WHERE p.id = l.parent)
            """
        )
    )

    # ---- backfill principals from role_assignments + group_memberships ----------
    op.execute(
        sa.text(
            """
            WITH refs AS (
                SELECT LOWER(ra.principal_id) AS id, ra.principal_name AS name,
                       ra.principal_type AS ptype, s.created_at AS seen
                  FROM role_assignments ra JOIN scans s ON s.id = ra.scan_id
                 WHERE s.status = 'done'
                UNION ALL
                SELECT LOWER(gm.member_id), gm.member_name, gm.member_type, s.created_at
                  FROM group_memberships gm JOIN scans s ON s.id = gm.scan_id
                 WHERE s.status = 'done'
            )
            INSERT INTO principals (id, display_name, principal_type, resolved_at,
                                    first_seen_at, last_seen_at)
            SELECT id,
                   (ARRAY_AGG(name  ORDER BY (name  IS NULL), seen DESC))[1],
                   (ARRAY_AGG(ptype ORDER BY (ptype IS NULL), seen DESC))[1],
                   MAX(seen) FILTER (WHERE name IS NOT NULL),
                   MIN(seen), MAX(seen)
              FROM refs
             WHERE id IS NOT NULL AND id <> ''
             GROUP BY id
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_principals_principal_type", table_name="principals")
    op.drop_table("principals")
    for name in ("ix_scopes_latest_scan_id", "ix_scopes_parent_id", "ix_scopes_level"):
        op.drop_index(name, table_name="scopes")
    op.drop_table("scopes")
