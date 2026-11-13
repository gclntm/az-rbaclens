"""rename scans.mgmt_group → scope; add scope_level

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-08

The scan target is no longer just a management group; it can be any
Azure RBAC scope (mgmt group, subscription, resource group, resource).
The persisted ``scope`` column holds the full ARM path; ``scope_level``
tags which kind of scope it is.

Existing rows are backfilled by prefixing the old ``mgmt_group`` value
with the well-known mgmt-group ARM path.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scans", sa.Column("scope", sa.Text(), nullable=True))
    op.add_column("scans", sa.Column("scope_level", sa.String(length=20), nullable=True))
    op.execute(
        """
        UPDATE scans
        SET scope = '/providers/Microsoft.Management/managementGroups/' || mgmt_group,
            scope_level = 'mgmt_group'
        """
    )
    op.alter_column("scans", "scope", nullable=False)
    op.alter_column("scans", "scope_level", nullable=False)
    op.drop_index("ix_scans_mgmt_group", table_name="scans")
    op.drop_column("scans", "mgmt_group")
    op.create_index("ix_scans_scope_level", "scans", ["scope_level"])


def downgrade() -> None:
    op.add_column("scans", sa.Column("mgmt_group", sa.String(length=255), nullable=True))
    op.execute(
        """
        UPDATE scans
        SET mgmt_group = regexp_replace(scope, '^.*/managementGroups/', '')
        WHERE scope_level = 'mgmt_group'
        """
    )
    op.alter_column("scans", "mgmt_group", nullable=False)
    op.create_index("ix_scans_mgmt_group", "scans", ["mgmt_group"])
    op.drop_index("ix_scans_scope_level", table_name="scans")
    op.drop_column("scans", "scope_level")
    op.drop_column("scans", "scope")
