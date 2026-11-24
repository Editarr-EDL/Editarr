"""Add community master filter sets and retire revision-link data without losing it.

New tables hold each account's master links, verified snapshots, per-filter local
state, pending updates, dismissals, preferences and daily sync state.

The revision protocol's records are kept:

- `community_imports` and `community_import_targets` are renamed with a `retired_`
  prefix; every column, BLOB and the `scans` cascade stays. Their scans and findings
  are untouched, so they remain plain local imported scans.
- `receipt` and `vote` operations are copied verbatim into
  `retired_community_operations` before they leave `community_operations`.
- Unfinished `publish` operations end `failed` with the publication error
  `protocol_retired`; acknowledged ones and all exact bytes stay as history.

`community_operations` is rebuilt for the `submission` and `filter_vote` kinds.
The adoption path runs this only after its verified, recoverable backup.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0037_community_master_sets"
down_revision = "0036_sampled_media_identity"
branch_labels = None
depends_on = None

_RETIRED_REASON = "revision_protocol_removed"
_OPERATION_COLUMNS = (
    "operation_id, service_url, kind, state, contributor_id, installation_id, "
    "contribution_id, revision_id, digest, scan_id, consent_version, receipt, vote, "
    "updated_at, owner_id, actor_id"
)
_UNFINISHED_PUBLISH = (
    "SELECT operation_id FROM community_operations "
    "WHERE kind = 'publish' AND state IN ('pending', 'submitted')"
)
_MASTER_TABLES = (
    "community_sync_notifications",
    "community_sync_state",
    "community_master_preferences",
    "community_master_dismissals",
    "community_master_updates",
    "community_master_filters",
    "community_master_snapshots",
    "community_master_links",
)


def _link_column():
    return sa.Column(
        "link_id",
        sa.String(36),
        sa.ForeignKey("community_master_links.id", ondelete="CASCADE"),
        primary_key=True,
    )


def _create_links():
    op.create_table(
        "community_master_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "media_version_id", sa.String(), sa.ForeignKey("media_versions.id"), nullable=False
        ),
        sa.Column("service_url", sa.String(256), nullable=False),
        sa.Column("contributor_id", sa.String(128), nullable=True),
        sa.Column("community_version_id", sa.String(128), nullable=False),
        sa.Column("community_work_id", sa.String(128), nullable=True),
        sa.Column("label", sa.String(256), nullable=True),
        sa.Column("alias_id", sa.String(128), nullable=True),
        sa.Column("version_status", sa.String(16), nullable=False),
        sa.Column("tier", sa.String(8), nullable=True),
        sa.Column("match_percent", sa.Integer(), nullable=True),
        sa.Column("applied_revision", sa.Integer(), nullable=False),
        sa.Column("latest_revision", sa.Integer(), nullable=False),
        sa.Column("alignment_state", sa.String(16), nullable=False),
        sa.Column("alignment_json", sa.Text(), nullable=True),
        sa.Column("alignment_revision", sa.Integer(), nullable=True),
        sa.Column("link_state", sa.String(16), nullable=False),
        sa.Column("merged_into", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "alignment_state IN ('not_required', 'required', 'approved')",
            name="ck_community_master_links_alignment_state",
        ),
        sa.CheckConstraint(
            "link_state IN ('active', 'merged', 'unavailable', 'replaced')",
            name="ck_community_master_links_link_state",
        ),
        sa.CheckConstraint(
            "match_percent IS NULL OR (match_percent >= 0 AND match_percent <= 100)",
            name="ck_community_master_links_match_percent",
        ),
        sa.CheckConstraint(
            "applied_revision >= 1 AND latest_revision >= applied_revision",
            name="ck_community_master_links_revisions",
        ),
    )
    op.create_index(
        "uq_community_master_links_live",
        "community_master_links",
        ["owner_id", "media_version_id", "service_url"],
        unique=True,
        sqlite_where=sa.text("link_state != 'replaced'"),
    )
    op.create_index(
        "ix_community_master_links_version",
        "community_master_links",
        ["owner_id", "community_version_id"],
    )


def _create_link_children():
    op.create_table(
        "community_master_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "link_id",
            sa.String(36),
            sa.ForeignKey("community_master_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("snapshot_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("signature_json", sa.Text(), nullable=False),
        sa.Column("service_key_id", sa.String(71), nullable=False),
        sa.Column("receipt_json", sa.Text(), nullable=False),
        sa.Column("receipt_id", sa.String(64), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("link_id", "revision", name="uq_community_master_snapshots_revision"),
    )
    op.create_table(
        "community_master_filters",
        _link_column(),
        sa.Column("filter_id", sa.String(128), primary_key=True),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("baseline_json", sa.Text(), nullable=False),
        sa.Column("baseline_revision", sa.Integer(), nullable=False),
        sa.Column("info_json", sa.Text(), nullable=False),
        sa.Column("local_start_ms", sa.Integer(), nullable=True),
        sa.Column("local_end_ms", sa.Integer(), nullable=True),
        sa.Column("local_subcategory", sa.String(128), nullable=True),
        sa.Column("local_severity", sa.String(16), nullable=True),
        sa.Column("local_action", sa.String(16), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("locally_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("local_touched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("my_vote", sa.String(4), nullable=True),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'verified', 'adjusted', 'rejected', 'low_confidence')",
            name="ck_community_master_filters_state",
        ),
        sa.CheckConstraint(
            "local_start_ms IS NULL OR local_start_ms >= 0",
            name="ck_community_master_filters_start",
        ),
        sa.CheckConstraint(
            "local_start_ms IS NULL OR local_end_ms IS NULL OR local_start_ms < local_end_ms",
            name="ck_community_master_filters_bounds",
        ),
        sa.CheckConstraint(
            "my_vote IS NULL OR my_vote IN ('up', 'down')",
            name="ck_community_master_filters_vote",
        ),
    )
    op.create_table(
        "community_master_updates",
        _link_column(),
        sa.Column("filter_id", sa.String(128), primary_key=True),
        sa.Column("to_revision", sa.Integer(), nullable=False),
        sa.Column("change", sa.String(16), nullable=False),
        sa.Column("changed_fields_json", sa.Text(), nullable=False),
        sa.Column("new_master_json", sa.Text(), nullable=False),
        sa.Column("merged_into", sa.String(128), nullable=True),
        sa.Column("values_digest", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "change IN ('new', 'changed', 'removed', 'low_confidence')",
            name="ck_community_master_updates_change",
        ),
    )
    op.create_table(
        "community_master_dismissals",
        _link_column(),
        sa.Column("filter_id", sa.String(128), primary_key=True),
        sa.Column("values_digest", sa.String(64), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=False),
    )


def _create_account_state():
    op.create_table(
        "community_master_preferences",
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("confidence_threshold", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("show_low_confidence", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "confidence_threshold >= 0 AND confidence_threshold <= 1",
            name="ck_community_master_preferences_threshold",
        ),
    )
    op.create_table(
        "community_sync_state",
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("service_url", sa.String(256), primary_key=True),
        sa.Column("contributor_id", sa.String(128), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("last_started_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("next_due_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "state IN ('idle', 'running', 'failed')", name="ck_community_sync_state_state"
        ),
    )
    op.create_table(
        "community_sync_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "link_id",
            sa.String(36),
            sa.ForeignKey("community_master_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("from_revision", sa.Integer(), nullable=True),
        sa.Column("to_revision", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('changed', 'merged', 'unavailable')",
            name="ck_community_sync_notifications_kind",
        ),
        sa.UniqueConstraint(
            "owner_id", "link_id", "to_revision", name="uq_community_sync_notifications_revision"
        ),
    )
    op.create_index(
        "ix_community_sync_notifications_unread",
        "community_sync_notifications",
        ["owner_id", "read_at"],
    )


def _create_retired_operations():
    op.create_table(
        "retired_community_operations",
        sa.Column("operation_id", sa.String(128), primary_key=True),
        sa.Column("service_url", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("contributor_id", sa.String(128), nullable=True),
        sa.Column("installation_id", sa.String(36), nullable=True),
        sa.Column("contribution_id", sa.String(128), nullable=True),
        sa.Column("revision_id", sa.String(128), nullable=True),
        sa.Column("digest", sa.String(71), nullable=True),
        sa.Column("scan_id", sa.String(), nullable=True),
        sa.Column("consent_version", sa.String(128), nullable=True),
        sa.Column("receipt", sa.String(2048), nullable=True),
        sa.Column("vote", sa.String(16), nullable=True),
        sa.Column("updated_at", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("retired_at", sa.String(64), nullable=False),
        sa.Column("retired_reason", sa.String(64), nullable=False),
    )


def _retire_revision_data(bind):
    now = datetime.now(timezone.utc).isoformat()
    op.rename_table("community_imports", "retired_community_imports")
    op.rename_table("community_import_targets", "retired_community_import_targets")
    bind.execute(
        sa.text(
            f"INSERT INTO retired_community_operations ({_OPERATION_COLUMNS}, retired_at, "
            f"retired_reason) SELECT {_OPERATION_COLUMNS}, :now, :reason "
            "FROM community_operations WHERE kind IN ('receipt', 'vote')"
        ),
        {"now": now, "reason": _RETIRED_REASON},
    )
    bind.execute(sa.text("DELETE FROM community_operations WHERE kind IN ('receipt', 'vote')"))
    bind.execute(
        sa.text(
            "UPDATE community_publications SET error_code = 'protocol_retired' "
            f"WHERE operation_id IN ({_UNFINISHED_PUBLISH})"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE community_operations SET state = 'failed', updated_at = :now "
            "WHERE kind = 'publish' AND state IN ('pending', 'submitted')"
        ),
        {"now": now},
    )


def _rebuild_operations(kinds):
    with op.batch_alter_table("community_operations", recreate="always") as batch:
        batch.drop_constraint("ck_community_operation_kind", type_="check")
        batch.create_check_constraint("ck_community_operation_kind", f"kind IN {kinds}")
        if kinds == ("publish", "submission", "filter_vote"):
            batch.add_column(sa.Column("community_version_id", sa.String(128), nullable=True))
            batch.add_column(sa.Column("filter_id", sa.String(128), nullable=True))
            batch.add_column(sa.Column("receipt_id", sa.String(64), nullable=True))
        else:
            for name in ("receipt_id", "filter_id", "community_version_id"):
                batch.drop_column(name)


def upgrade():
    bind = op.get_bind()
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding community_operations requires foreign keys off")
    _create_links()
    _create_link_children()
    _create_account_state()
    _create_retired_operations()
    _retire_revision_data(bind)
    _rebuild_operations(("publish", "submission", "filter_vote"))


def downgrade():
    """Undo only when nothing would be lost: no master-set data and no retired publication."""
    bind = op.get_bind()
    held = (
        any(
            bind.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first()
            for table in _MASTER_TABLES
        )
        or bind.exec_driver_sql(
            "SELECT 1 FROM community_operations WHERE kind != 'publish' LIMIT 1"
        ).first()
    )
    if held:
        raise RuntimeError("Restore a backup to downgrade without discarding master-set data")
    if bind.exec_driver_sql(
        "SELECT 1 FROM community_publications WHERE error_code = 'protocol_retired' LIMIT 1"
    ).first():
        raise RuntimeError("Restore a backup to recover publications the upgrade retired")
    if bind.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Rebuilding community_operations requires foreign keys off")
    _rebuild_operations(("publish", "receipt", "vote"))
    bind.execute(
        sa.text(
            f"INSERT INTO community_operations ({_OPERATION_COLUMNS}) "
            f"SELECT {_OPERATION_COLUMNS} FROM retired_community_operations"
        )
    )
    op.drop_table("retired_community_operations")
    op.rename_table("retired_community_import_targets", "community_import_targets")
    op.rename_table("retired_community_imports", "community_imports")
    for table in _MASTER_TABLES:
        op.drop_table(table)
