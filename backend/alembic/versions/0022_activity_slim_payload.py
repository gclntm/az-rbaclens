"""activity_events: caller_oid column, slimmed raw payload, index cleanup

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-22

Phase 5 of docs/redesign-plan.md. ``raw`` was most of the table's size
(a few KB per event) and the app reads one value from it, the caller's
object id. This migration:

1. adds ``caller_oid`` and backfills it from the JWT claims;
2. slims ``raw`` with the rules in ``app/core/activity_payload.py``
   (drop keys stored as columns, keep a claims subset) — mirrored in SQL
   below; the two must change together;
3. swaps 0021's JSON-path expression index for a plain
   ``(caller_oid, occurred_at)`` index and drops three B-trees no query
   can use (the caller / operation filters are ``LIKE '%x%'``).

Steps 1–2 run in id batches that commit independently, so an
interrupted run loses at most one batch and can simply be re-run (the
rules are idempotent). Freed space stays inside the table until
``VACUUM FULL`` — Settings → Database, or ``POST
/settings/database/vacuum?full=true``.

Downgrade restores the indexes and drops ``caller_oid``; the dropped
payload keys are gone for good.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH = 20_000

_OID = "http://schemas.microsoft.com/identity/claims/objectidentifier"
_UPN = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn"
# activity_payload.DROPPED_KEYS, plus ``claims`` (rebuilt as a subset).
_DROPPED = "{id,event_data_id,subscription_id,caller,operation_name,status,resource_id,resource_type,correlation_id,event_timestamp,claims}"


def _claim(*names: str) -> str:
    return "coalesce(" + ", ".join(f"nullif(raw #>> '{{claims,{n}}}', '')" for n in names) + ")"


_SLIM_SQL = f"""
UPDATE activity_events SET
  caller_oid = lower({_claim(_OID, "oid")}),
  raw = (raw - '{_DROPPED}'::text[])
        || CASE WHEN jsonb_typeof(raw -> 'claims') = 'object' THEN
             jsonb_build_object('claims', jsonb_strip_nulls(jsonb_build_object(
               'oid', {_claim(_OID, "oid")},
               'appid', {_claim("appid")},
               'idtyp', {_claim("idtyp")},
               'upn', {_claim(_UPN, "upn")},
               'name', {_claim("name")},
               'ipaddr', {_claim("ipaddr")})))
           ELSE '{{}}'::jsonb END
WHERE id >= :lo AND id < :hi AND raw IS NOT NULL
"""

_UNUSED_INDEXES = {
    "ix_activity_events_caller": "(caller)",
    "ix_activity_events_caller_time": "(caller, occurred_at)",
    "ix_activity_events_operation_name": "(operation_name)",
}
_OID_EXPR_0021 = (
    "lower(coalesce("
    f"CAST(raw #>> '{{claims, {_OID}}}' AS VARCHAR), "
    "CAST(raw #>> '{claims, oid}' AS VARCHAR)))"
)


def upgrade() -> None:
    op.add_column("activity_events", sa.Column("caller_oid", sa.String(length=64), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        op.create_index(
            "ix_activity_events_oid_time", "activity_events", ["caller_oid", "occurred_at"]
        )
        return

    with op.get_context().autocommit_block():
        lo, hi = bind.execute(sa.text("SELECT min(id), max(id) FROM activity_events")).one()
        if lo is not None:
            for start in range(lo, hi + 1, _BATCH):
                bind.execute(sa.text(_SLIM_SQL), {"lo": start, "hi": start + _BATCH})
                done = min(start + _BATCH - lo, hi - lo + 1)
                print(
                    f"  0022: slimmed ids {lo}..{start + _BATCH - 1} ({done * 100 // (hi - lo + 1)} %)"
                )
        bind.execute(sa.text("DROP INDEX IF EXISTS ix_activity_events_caller_oid"))
        for name in _UNUSED_INDEXES:
            bind.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))
        bind.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS ix_activity_events_oid_time "
                "ON activity_events (caller_oid, occurred_at)"
            )
        )
        bind.execute(sa.text("ANALYZE activity_events"))


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_activity_events_oid_time", table_name="activity_events")
    if bind.dialect.name == "postgresql":
        for name, cols in _UNUSED_INDEXES.items():
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON activity_events {cols}")
        # Slimmed payloads keep ``claims.oid``, so 0021's expression still works.
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_activity_events_caller_oid ON activity_events ({_OID_EXPR_0021})"
        )
    op.drop_column("activity_events", "caller_oid")
