"""What an audited change changed

Revision ID: 0042
Revises: 0041
Create Date: 2026-11-18

``audit_events.detail``: set by an endpoint that wants to say what it changed
(Settings › Data kept: the values before and after). Request bodies are still
never recorded: they may hold secrets.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as t:
        t.add_column(sa.Column("detail", _JSON))


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as t:
        t.drop_column("detail")
