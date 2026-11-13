"""Audit log: the caller's address

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-09

``audit_events.client_ip`` records where each change came from: the address
nginx saw, passed on in ``X-Forwarded-For`` (core/audit.py). Older rows stay
empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("client_ip", sa.String(length=45), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_events", "client_ip")
