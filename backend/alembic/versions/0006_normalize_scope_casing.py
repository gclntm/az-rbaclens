"""normalize scope casing + recompute scope_level

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-08

Earlier scans persisted scope strings in whatever casing Azure returned
(mixed: ``managementGroups`` vs ``managementgroups``, ``microsoft.management``
vs ``Microsoft.Management``, …). That broke ``in_tree`` membership in the
/tree endpoint and caused some MG-anchored RAs to be classified as
``resource``. We now normalize all scope strings to lowercase at ingestion;
this migration backfills existing rows so legacy scans render correctly
without a re-scan.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Lower-case every scope-shaped column.
    op.execute("UPDATE scans SET scope = lower(scope)")
    op.execute("UPDATE role_assignments SET scope = lower(scope)")

    # Re-derive scope_level from the (now lower-cased) scope so rows that
    # were mis-classified as ``resource`` (because /managementgroups/ didn't
    # match /managementGroups/) flip back to the right level.
    op.execute(
        """
        UPDATE role_assignments
        SET scope_level = CASE
            WHEN scope LIKE '%/managementgroups/%' THEN 'mgmt_group'
            WHEN scope LIKE '/subscriptions/%/resourcegroups/%/providers/%' THEN 'resource'
            WHEN scope LIKE '/subscriptions/%/resourcegroups/%' THEN 'resource_group'
            WHEN scope LIKE '/subscriptions/%' THEN 'subscription'
            ELSE 'resource'
        END
        """
    )

    # Lower-case the keys / parent fields inside scans.hierarchy JSONB so
    # the tree endpoint's ``in_tree`` membership matches normalized RA
    # scopes consistently.
    op.execute(
        """
        UPDATE scans
        SET hierarchy = (
            SELECT jsonb_object_agg(
                lower(key),
                CASE
                    WHEN value ? 'parent' AND value->>'parent' IS NOT NULL
                    THEN jsonb_set(value, '{parent}', to_jsonb(lower(value->>'parent')))
                    ELSE value
                END
            )
            FROM jsonb_each(hierarchy)
        )
        WHERE hierarchy IS NOT NULL
        """
    )


def downgrade() -> None:
    # No-op: we can't recover the original casings from the lower-cased data.
    pass
