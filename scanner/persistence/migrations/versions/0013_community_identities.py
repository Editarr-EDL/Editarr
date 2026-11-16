"""Give each account its own community identity and add stored audit events.

Upgrade creates one identity row with a random credential reference for the owner,
or awaiting owner setup when there is none. Every existing connection moves to that
identity, and every receipt, vote, and publication operation becomes the owner's
contributor history. Their `actor_id` stays null because nobody recorded who acted.
No private file moves here; the application moves the signing files into the
identity's directory with one rename. `audit_events` starts empty.
"""

import secrets
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0013_community_identities"
down_revision = "0012_personal_filter_values"
branch_labels = None
depends_on = None

_OWNER = "(SELECT id FROM users WHERE role = 'owner')"
_CONNECTION_COLUMNS = (
    "service_url, contributor_id, installation_id, key_id, sharing_enabled, consent_version, "
    "consented_at"
)


def _create_identities():
    op.create_table(
        "community_identities",
        sa.Column("credential_ref", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(credential_ref) = 32 AND credential_ref NOT GLOB '*[^0-9a-f]*'",
            name="ck_community_identities_credential_ref",
        ),
    )
    reference = secrets.token_hex(16)
    op.get_bind().execute(
        sa.text(
            "INSERT INTO community_identities (credential_ref, user_id, created_at) "
            f"VALUES (:reference, {_OWNER}, :now)"
        ),
        {
            "reference": reference,
            "now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"),
        },
    )
    return reference


def _rebuild_connections(reference):
    op.create_table(
        "community_connections_0013",
        sa.Column(
            "credential_ref",
            sa.String(32),
            sa.ForeignKey("community_identities.credential_ref"),
            primary_key=True,
        ),
        sa.Column("service_url", sa.String(256), primary_key=True),
        sa.Column("contributor_id", sa.String(128), nullable=True),
        sa.Column("installation_id", sa.String(36), nullable=True),
        sa.Column("key_id", sa.String(71), nullable=True),
        sa.Column("sharing_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("consent_version", sa.String(128), nullable=True),
        sa.Column("consented_at", sa.String(64), nullable=True),
        sa.CheckConstraint("length(service_url) > 0", name="ck_community_connection_service"),
        sa.CheckConstraint("sharing_enabled IN (0, 1)", name="ck_community_connection_sharing"),
    )
    op.get_bind().execute(
        sa.text(
            f"INSERT INTO community_connections_0013 (credential_ref, {_CONNECTION_COLUMNS}) "
            f"SELECT :reference, {_CONNECTION_COLUMNS} FROM community_connections "
            "ORDER BY rowid"
        ),
        {"reference": reference},
    )
    op.execute("DROP TABLE community_connections")
    op.execute("ALTER TABLE community_connections_0013 RENAME TO community_connections")


def _attribute_operations():
    op.execute("ALTER TABLE community_operations ADD COLUMN owner_id INTEGER REFERENCES users (id)")
    op.execute("ALTER TABLE community_operations ADD COLUMN actor_id INTEGER REFERENCES users (id)")
    op.create_index("ix_community_operations_owner_id", "community_operations", ["owner_id"])
    op.execute(f"UPDATE community_operations SET owner_id = {_OWNER}")


def _create_audit_events():
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(9), nullable=False),
        sa.Column("actor_kind", sa.String(12), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("credential", sa.String(7), nullable=True),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("media_key", sa.String(), nullable=True),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.CheckConstraint("length(event_id) = 36", name="ck_audit_events_event_id"),
        sa.CheckConstraint("length(action) BETWEEN 1 AND 64", name="ck_audit_events_action"),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed')", name="ck_audit_events_outcome"
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'system', 'anonymous', 'unattributed')",
            name="ck_audit_events_actor_kind",
        ),
        sa.CheckConstraint(
            "(actor_kind = 'user') = (actor_id IS NOT NULL)", name="ck_audit_events_actor"
        ),
        sa.CheckConstraint(
            "credential IS NULL OR (actor_kind = 'user' AND credential IN ('session', 'api_key'))",
            name="ck_audit_events_credential",
        ),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_target_user_id", "audit_events", ["target_user_id"])


def upgrade():
    if op.get_bind().exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding community_connections requires foreign keys off")
    _rebuild_connections(_create_identities())
    _attribute_operations()
    _create_audit_events()


def downgrade():
    raise RuntimeError("Local migrations cannot discard community identities or audit events")
