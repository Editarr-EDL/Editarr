"""Give every account its own filter mode.

`filter_settings.default_mode` becomes only the mode new accounts start in. Each
account that followed it gets that mode as its own: a null mode is set to it, and
an account without a `user_filter_preferences` row gets one at revision 0. An
account that ends in copy mode without a copy gets a copy of the current instance
rules and severities, which is what it saw before. `mode` then becomes NOT NULL.
Enforcement rows, revisions, and policy revisions stay as they are.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0031_explicit_filter_modes"
down_revision = "0030_remove_passwords"
branch_labels = None
depends_on = None

_RULE_COLUMNS = (
    "disposition",
    "evidence_source",
    "category",
    "subcategory",
    "label_contains",
    "severity",
    "mcf_channel",
)
_WITHOUT_COPY = (
    "SELECT user_id FROM user_filter_preferences WHERE mode = 'copy' "
    "AND user_id NOT IN (SELECT user_id FROM user_filter_copy_severities)"
)


def upgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding user_filter_preferences requires foreign keys off")
    values = {
        "now": datetime.now(timezone.utc).replace(tzinfo=None),
        "mode": bind.execute(sa.text("SELECT default_mode FROM filter_settings")).scalar_one(),
    }
    bind.execute(
        sa.text(
            "UPDATE user_filter_preferences SET mode = :mode, updated_at = :now WHERE mode IS NULL"
        ),
        values,
    )
    bind.execute(
        sa.text(
            "INSERT INTO user_filter_preferences (user_id, mode, revision, policy_revision, "
            "updated_at) SELECT id, :mode, 0, 0, :now FROM users "
            "WHERE id NOT IN (SELECT user_id FROM user_filter_preferences)"
        ),
        values,
    )
    _copy_instance_defaults(bind)
    with op.batch_alter_table("user_filter_preferences", recreate="always") as batch:
        batch.alter_column("mode", existing_type=sa.String(4), nullable=False)


def _copy_instance_defaults(bind):
    """Copy the instance rules and severities, in order, for copy-mode accounts without a copy."""
    columns = ", ".join(_RULE_COLUMNS)
    bind.execute(
        sa.text(
            f"INSERT INTO user_filter_copy_rules (user_id, id, position, {columns}) "
            "SELECT owner.user_id, rule.id, "
            "row_number() OVER (PARTITION BY owner.user_id ORDER BY rule.position) - 1, "
            f"{', '.join(f'rule.{name}' for name in _RULE_COLUMNS)} "
            f"FROM ({_WITHOUT_COPY}) AS owner CROSS JOIN filter_selection_rules AS rule "
            "WHERE rule.settings_id = 1"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO user_filter_copy_severities (user_id, subcategory, position, severity) "
            "SELECT owner.user_id, item.subcategory, "
            "row_number() OVER (PARTITION BY owner.user_id ORDER BY item.position) - 1, "
            f"item.severity FROM ({_WITHOUT_COPY}) AS owner "
            "CROSS JOIN mcf_severity_defaults AS item WHERE item.settings_id = 1"
        )
    )


def downgrade():
    raise RuntimeError("Local migrations cannot restore which accounts followed the default mode")
