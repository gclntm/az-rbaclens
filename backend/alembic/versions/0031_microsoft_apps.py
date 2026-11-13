"""Who publishes each app (Microsoft's own apps)

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-08

Service principals carry the tenant that publishes the app
(``appOwnerOrganizationId``) and their kind (``servicePrincipalType``),
read from Graph at each check: on the grant rows, so a registry rebuild
keeps them, and on ``principals``. The advice uses them to recognise
Microsoft's own apps (Defender for Cloud, AKS…). Filled from the next check.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("role_assignments", sa.Column("principal_app_owner", sa.String(length=64)))
    op.add_column("role_assignments", sa.Column("principal_sp_type", sa.String(length=30)))
    op.add_column("principals", sa.Column("app_owner_org", sa.String(length=64)))
    op.add_column("principals", sa.Column("sp_type", sa.String(length=30)))


def downgrade() -> None:
    op.drop_column("principals", "sp_type")
    op.drop_column("principals", "app_owner_org")
    op.drop_column("role_assignments", "principal_sp_type")
    op.drop_column("role_assignments", "principal_app_owner")
