"""Remove stored grants of the retired community import-receipt capabilities.

`community.receipts.recover.own` and `.other` governed only the revision
protocol's import-receipt routes, which 0037 retired. The grant loader already
ignores capabilities outside the catalog, so removing their role-default and
user-override rows changes no decision. The adoption path runs this only after
its verified, recoverable backup, which keeps the removed rows.
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_retire_receipt_capabilities"
down_revision = "0037_community_master_sets"
branch_labels = None
depends_on = None

RETIRED_CAPABILITIES = ("community.receipts.recover.own", "community.receipts.recover.other")


def upgrade():
    bind = op.get_bind()
    for table in ("role_capability_defaults", "user_capability_overrides"):
        bind.execute(
            sa.text(f"DELETE FROM {table} WHERE capability IN (:own, :other)"),
            dict(zip(("own", "other"), RETIRED_CAPABILITIES, strict=True)),
        )


def downgrade():
    """Nothing to restore: a missing row means the catalog default at 0037."""
