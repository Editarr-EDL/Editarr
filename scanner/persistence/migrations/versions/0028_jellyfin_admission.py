"""Admit every Jellyfin user at first sign-in and stop mirroring Jellyfin roles.

Import-only admission is gone, so `account_settings.jellyfin_admission` is
dropped. An install that stored `import_only` gets one system audit event that
records the switch to first-sign-in admission. Jellyfin administrator status now
sets only an account's starting role, so `user_provider_identities.role_explicit`
is dropped too; every role stays as stored.
"""

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0028_jellyfin_admission"
down_revision = "0027_jellyfin_owner"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    stored = bind.execute(sa.text("SELECT jellyfin_admission FROM account_settings")).scalar()
    if stored == "import_only":
        bind.execute(
            sa.text(
                "INSERT INTO audit_events (event_id, occurred_at, action, outcome, actor_kind, "
                "details_json) VALUES (:event_id, :occurred_at, "
                "'settings.jellyfin_admission.change', 'succeeded', 'system', :details)"
            ),
            {
                "event_id": str(uuid.uuid4()),
                "occurred_at": datetime.now(timezone.utc).replace(tzinfo=None),
                "details": json.dumps(
                    {"mode": "first_login", "previous": "import_only"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        )
    # SQLite 3.35's DROP COLUMN removes each column together with the inline CHECK 0018
    # declared on it and rewrites nothing else. A batch rebuild would copy both tables and
    # recreate all their other columns and constraints, which this change does not need.
    op.execute("ALTER TABLE account_settings DROP COLUMN jellyfin_admission")
    op.execute("ALTER TABLE user_provider_identities DROP COLUMN role_explicit")


def downgrade():
    raise RuntimeError("Local migrations cannot restore import-only admission or role mirroring")
