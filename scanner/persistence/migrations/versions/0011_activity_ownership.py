"""Record who owns, started, and published local activity, and per-user policy storage.

Existing reviews, scans, and export records are assigned to the owner. Without an
owner they keep null owner columns, the explicit awaiting-owner state that owner
setup resolves. Export records become `legacy` records of the adjacent sidecars
they describe; no file is read or written. Filter modes, enforcement, and local
media access start empty apart from the all-sources local default.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0011_activity_ownership"
down_revision = "0010_user_accounts"
branch_labels = None
depends_on = None

_OWNER = "(SELECT id FROM users WHERE role = 'owner')"
_OWNER_REVISION = "(SELECT permission_revision FROM users WHERE role = 'owner')"
_EXPORT_COLUMNS = (
    "id, media_item_id, media_source_id, format, exported_at, review_id, exported_format_revision"
)


def _add_owner_columns():
    op.execute("ALTER TABLE saved_reviews ADD COLUMN owner_id INTEGER REFERENCES users (id)")
    op.execute(
        "ALTER TABLE saved_reviews ADD COLUMN original_author_id INTEGER REFERENCES users (id) "
        "CONSTRAINT ck_saved_reviews_owner_pair "
        "CHECK ((owner_id IS NULL) = (original_author_id IS NULL))"
    )
    op.create_index("ix_saved_reviews_owner_id", "saved_reviews", ["owner_id"])
    op.execute(f"UPDATE saved_reviews SET owner_id = {_OWNER}, original_author_id = {_OWNER}")

    op.execute("ALTER TABLE scans ADD COLUMN initiator_id INTEGER REFERENCES users (id)")
    op.execute(
        "ALTER TABLE scans ADD COLUMN initiator_permission_revision INTEGER "
        "CONSTRAINT ck_scans_initiator_pair CHECK ((initiator_id IS NULL) = "
        "(initiator_permission_revision IS NULL) AND (initiator_permission_revision IS NULL "
        "OR initiator_permission_revision >= 1))"
    )
    op.create_index("ix_scans_initiator_id", "scans", ["initiator_id"])
    op.execute(
        f"UPDATE scans SET initiator_id = {_OWNER}, initiator_permission_revision = "
        f"{_OWNER_REVISION}"
    )


def _rebuild_exports():
    op.create_table(
        "media_item_exports_0011",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_item_id", sa.String(), sa.ForeignKey("media_items.id"), nullable=False),
        sa.Column("media_source_id", sa.String(), nullable=False),
        sa.Column("format", sa.String(3), nullable=False),
        sa.Column("exported_at", sa.DateTime(), nullable=False),
        sa.Column("review_id", sa.String(), sa.ForeignKey("saved_reviews.id"), nullable=True),
        sa.Column("exported_format_revision", sa.Integer(), nullable=True),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("target_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("target_export_id", sa.String(), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("policy_owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("original_author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("policy_revision", sa.Integer(), nullable=True),
        sa.CheckConstraint("format IN ('edl', 'mcf')", name="ck_media_item_exports_format"),
        sa.CheckConstraint(
            "(review_id IS NULL AND exported_format_revision IS NULL) OR "
            "(review_id IS NOT NULL AND exported_format_revision IS NOT NULL "
            "AND exported_format_revision >= 0)",
            name="ck_media_item_exports_review_provenance",
        ),
        sa.CheckConstraint("format IN ('edl', 'mcf')", name="exportformat"),
        sa.CheckConstraint("scope IN ('legacy', 'default', 'personal')", name="exportscope"),
        sa.CheckConstraint(
            "(scope = 'personal') = (target_user_id IS NOT NULL) "
            "AND (target_user_id IS NULL) = (target_export_id IS NULL)",
            name="ck_media_item_exports_target",
        ),
        sa.CheckConstraint(
            "target_export_id IS NULL OR (length(target_export_id) = 38 "
            "AND substr(target_export_id, 1, 6) = 'local-' "
            "AND substr(target_export_id, 7) NOT GLOB '*[^0-9a-f]*') OR "
            "(length(target_export_id) = 32 AND target_export_id NOT GLOB '*[^0-9a-f]*' "
            "AND target_export_id != '00000000000000000000000000000000')",
            name="ck_media_item_exports_target_export_id",
        ),
        sa.CheckConstraint(
            "(policy_owner_id IS NULL) = (original_author_id IS NULL)",
            name="ck_media_item_exports_authorship_pair",
        ),
        sa.CheckConstraint(
            "scope = 'legacy' OR (actor_id IS NOT NULL AND policy_owner_id IS NOT NULL "
            "AND policy_revision IS NOT NULL)",
            name="ck_media_item_exports_provenance",
        ),
        sa.CheckConstraint(
            "actor_id IS NOT NULL OR policy_owner_id IS NULL",
            name="ck_media_item_exports_awaiting_owner",
        ),
        sa.CheckConstraint(
            "policy_revision IS NULL OR policy_revision >= 0",
            name="ck_media_item_exports_policy_revision",
        ),
    )
    op.execute(
        f"INSERT INTO media_item_exports_0011 ({_EXPORT_COLUMNS}, scope, actor_id, "
        "policy_owner_id, original_author_id) "
        f"SELECT {_EXPORT_COLUMNS}, 'legacy', {_OWNER}, "
        f"CASE WHEN review_id IS NULL THEN NULL ELSE {_OWNER} END, "
        f"CASE WHEN review_id IS NULL THEN NULL ELSE {_OWNER} END "
        "FROM media_item_exports ORDER BY id"
    )
    op.execute("DROP TABLE media_item_exports")
    op.execute("ALTER TABLE media_item_exports_0011 RENAME TO media_item_exports")
    op.create_index(
        "uq_media_item_exports_personal",
        "media_item_exports",
        ["media_item_id", "media_source_id", "format", "target_user_id"],
        unique=True,
        sqlite_where=sa.text("scope = 'personal'"),
    )
    op.create_index(
        "uq_media_item_exports_shared",
        "media_item_exports",
        ["media_item_id", "media_source_id", "format", "scope"],
        unique=True,
        sqlite_where=sa.text("scope != 'personal'"),
    )
    op.create_index(
        "ix_media_item_exports_policy_owner_id", "media_item_exports", ["policy_owner_id"]
    )


def _create_filter_policy_tables():
    op.execute(
        "ALTER TABLE filter_settings ADD COLUMN default_mode VARCHAR(4) DEFAULT 'live' NOT NULL "
        "CONSTRAINT filterpreferencemode CHECK (default_mode IN ('live', 'copy'))"
    )
    op.create_table(
        "user_filter_preferences",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mode", sa.String(4), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("mode IN ('live', 'copy')", name="filterpreferencemode"),
        sa.CheckConstraint("revision >= 0", name="ck_user_filter_preferences_revision"),
        sa.CheckConstraint(
            "policy_revision >= 0", name="ck_user_filter_preferences_policy_revision"
        ),
    )
    op.create_table(
        "user_filter_enforcements",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user_filter_preferences.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(14), primary_key=True),
        sa.Column("target", sa.String(), primary_key=True),
        sa.CheckConstraint("kind IN ('selection_rule', 'severity')", name="filterenforcementkind"),
        sa.CheckConstraint("length(target) > 0", name="ck_user_filter_enforcements_target"),
    )


def _create_media_access_tables():
    op.create_table(
        "media_access_scopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("all_sources", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR id = 1", name="ck_media_access_scopes_local_default"
        ),
        sa.UniqueConstraint("user_id", name="uq_media_access_scopes_user_id"),
    )
    op.create_table(
        "media_access_scope_sources",
        sa.Column(
            "scope_id",
            sa.Integer(),
            sa.ForeignKey("media_access_scopes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_kind", sa.String(16), primary_key=True),
        sa.Column("source_id", sa.String(), primary_key=True),
        sa.CheckConstraint(
            "source_kind IN ('jellyfin_library', 'radarr', 'sonarr', 'manual_files')",
            name="mediasourcekind",
        ),
        sa.CheckConstraint(
            "(source_kind = 'jellyfin_library') = (length(source_id) > 0)",
            name="ck_media_access_scope_sources_source_id",
        ),
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    op.get_bind().execute(
        sa.text(
            "INSERT INTO media_access_scopes (id, user_id, all_sources, updated_at) "
            "VALUES (1, NULL, 1, :now)"
        ),
        {"now": now},
    )


def upgrade():
    if op.get_bind().exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding media_item_exports requires foreign keys off")
    _add_owner_columns()
    _rebuild_exports()
    _create_filter_policy_tables()
    _create_media_access_tables()


def downgrade():
    raise RuntimeError("Local migrations cannot discard activity ownership")
