"""activity_events: expression index on the caller's object id

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-22

Redesign, phase 4. The principal page lists a
principal's own Activity Log events, matched on the JWT object-id
claim inside ``raw``. Without an index that is a full scan of the
table (~7 s for 2 M events). The indexed expression must stay
identical to ``app.core.scan_coverage.caller_oid_expr`` — Postgres
only uses an expression index for a query that repeats it.

Postgres only: SQLite test databases are built from the models.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXPR = (
    "lower(coalesce("
    "CAST(raw #>> '{claims, http://schemas.microsoft.com/identity/claims/objectidentifier}' AS VARCHAR), "
    "CAST(raw #>> '{claims, oid}' AS VARCHAR)))"
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"CREATE INDEX IF NOT EXISTS ix_activity_events_caller_oid ON activity_events ({_EXPR})"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_activity_events_caller_oid")
