"""Populated supported-revision databases and the invariants revisions 0011 and 0012 promise.

Fixture version: mu03-activity-fixture-2. Every supported source revision gets two
reviews, a completed, a queued, and an imported scan, a queued job, an EDL export
from a review, a scan-only MCF export, and real adjacent `Movie.edl`/`Movie.mcf`
sidecars, all on one Other Files item. From 0003 the imported scan also has a
community import record. A 0011 source with an owner also has the owner's filter
mode and one enforced severity.
"""

import hashlib
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

from scanner.persistence.migrations import adoption
from tests.account_migration_support import assert_preserved_after_account_migration
from tests.community_master_migration_support import (
    comparable,
    legacy_rows,
    retire_publications,
)
from tests.test_account_migration import _legacy_database

ACCOUNTS = "0010_user_accounts"
ACTIVITY = "0011_activity_ownership"
SUPPORTED = [
    None,
    "0001_current_baseline",
    "0002_scan_provenance",
    "0003_local_community",
    "0004_media_identity",
    "0005_audio_tracks",
    "0006_community_evidence",
    "0007_community_publications",
    "0008_api_key",
    "0009_readable_api_key",
    ACCOUNTS,
    ACTIVITY,
]
_COMMUNITY = set(SUPPORTED[SUPPORTED.index("0003_local_community") :])
_ACCOUNT_MIGRATED = frozenset({ACCOUNTS, ACTIVITY})
_ACCOUNT_TABLES = frozenset({"admin_users", "auth_sessions", "api_keys", "users"})
# Jellyfin owner setup stores its connection, link, and first read alongside the owner.
_SETUP_TABLES = frozenset(
    {"jellyfin_settings", "account_settings", "user_provider_identities", "jellyfin_account_sync"}
)
# Owner setup fills these on a 0011 source; assert_assigned checks them explicitly.
_ASSIGNED = frozenset(
    {
        "owner_id",
        "original_author_id",
        "initiator_id",
        "initiator_permission_revision",
        "actor_id",
        "policy_owner_id",
    }
)
_POLICY_TABLES = (
    "media_access_scope_sources",
    "user_filter_enforcements",
)
PERSONAL_VALUE_TABLES = (
    "user_filter_copy_rules",
    "user_filter_copy_severities",
    "user_filter_severity_overrides",
)
_TIME = "2026-02-01 00:00:00.000000"
SIDECARS = {"Movie.mkv": b"synthetic media bytes\n", "Movie.edl": b"1.0\t2.0\t1\n"}
SIDECARS["Movie.mcf"] = b"<!-- synthetic legacy MCF sidecar -->\n"


def _upgrade(path, revision):
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            configuration = Config()
            configuration.set_main_option("script_location", str(Path(adoption.__file__).parent))
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, revision)
    finally:
        engine.dispose()


def _clone(database, table, source_id, **changes):
    """Copy one row by ID with replaced columns, whatever the revision's other columns are."""
    columns = [row[1] for row in database.execute(f'PRAGMA table_info("{table}")')]
    selected = ", ".join("?" if name in changes else f'"{name}"' for name in columns)
    names = ", ".join(f'"{name}"' for name in columns)
    database.execute(
        f'INSERT INTO "{table}" ({names}) SELECT {selected} FROM "{table}" WHERE id = ?',
        [*(changes[name] for name in columns if name in changes), source_id],
    )


def build_activity_database(
    path, media_dir, revision, *, administrator=True, queued_status="queued"
):
    """Create a populated database at ``revision`` and its sidecars.

    Returns the legacy session cookie and the sidecar state. A live instance passes
    ``queued_status="awaiting_model_consent"`` so its queue never starts the job.
    """
    cookie = _legacy_database(
        path,
        "0009_readable_api_key" if revision in _ACCOUNT_MIGRATED else revision,
        administrator=administrator,
    )
    if revision in _ACCOUNT_MIGRATED:
        _upgrade(path, ACCOUNTS)
    media_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in SIDECARS.items():
        (media_dir / name).write_bytes(payload)
    media = str((media_dir / "Movie.mkv").resolve())
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE media_items SET server_id = 'editarr-manual', jellyfin_id = ?, path = ?, "
            "item_type = 'Video', name = 'Movie.mkv'",
            (f"manual:{media}", media),
        )
        database.execute(
            "UPDATE scans SET source_path = ?, resolved_local_path = ?, status = 'complete'",
            (media, media),
        )
        _clone(
            database,
            "scans",
            "legacy-scan-001",
            id="queued-scan-002",
            scan_number=8,
            name="Queued specimen",
            status=queued_status,
        )
        _clone(
            database,
            "scans",
            "legacy-scan-001",
            id="imported-scan-003",
            scan_number=9,
            name="Imported specimen",
            origin="imported",
            import_source_name="Movie.mcf",
            imported_at=_TIME,
        )
        database.execute(
            "INSERT INTO scan_queue (scan_id, sort_order, created_at, updated_at) "
            "VALUES ('queued-scan-002', 0, ?, ?)",
            (_TIME, _TIME),
        )
        _clone(
            database,
            "saved_reviews",
            "review-legacy-001",
            id="review-imported-002",
            name="Imported review",
            revision=5,
            content_revision=4,
            edl_content_revision=3,
            mcf_content_revision=2,
        )
        database.execute(
            "INSERT INTO review_scan_memberships (review_id, scan_id, role, position) "
            "VALUES ('review-imported-002', 'imported-scan-003', 'both', 0)"
        )
        database.execute(
            "INSERT INTO review_synchronizations (review_id, start_seconds, end_seconds, "
            "end_mode) VALUES ('review-imported-002', 0.0, 120.0, 'file_end')"
        )
        database.execute(
            "INSERT INTO media_item_exports (id, media_item_id, media_source_id, format, "
            "exported_at, review_id, exported_format_revision) VALUES "
            "(1, 'media-legacy', '', 'edl', ?, 'review-legacy-001', 1), "
            "(2, 'media-legacy', '', 'mcf', ?, NULL, NULL)",
            (_TIME, _TIME),
        )
        database.execute(
            "INSERT INTO media_item_export_scans (media_item_export_id, scan_id, role) VALUES "
            "(1, 'legacy-scan-001', 'audio'), (2, 'imported-scan-003', 'both')"
        )
        if revision in _COMMUNITY:
            database.execute(
                "INSERT INTO community_imports (id, scan_id, service_url, contribution_id, "
                "revision_id, digest, author_id, artifact_type) VALUES ('community-import', "
                "'imported-scan-003', 'https://community.example', 'root', 'revision', "
                "'sha256:123', 'author', 'scan')"
            )
    if revision == ACTIVITY:
        _upgrade(path, ACTIVITY)
        if administrator:
            with sqlite3.connect(path) as database:
                database.execute(
                    "INSERT INTO user_filter_preferences VALUES (1, 'copy', 2, 1, ?)", (_TIME,)
                )
                database.execute(
                    "INSERT INTO user_filter_enforcements VALUES (1, 'severity', 'swearing')"
                )
    return cookie, sidecar_state(media_dir)


def link_owner(path) -> None:
    """Link the upgraded owner to a Jellyfin user and enable it, as linking it would.

    0030 disables an owner without a Jellyfin link, and restore takes only a linked owner.
    """
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT INTO user_provider_identities (user_id, server_id, provider_user_id, "
            "link_state, created_at, updated_at) SELECT id, 'server', ?, 'verified', ?, ? "
            "FROM users WHERE role = 'owner'",
            ("1" * 32, "2026-11-03", "2026-11-03"),
        )
        database.execute(
            "UPDATE users SET status = 'active', disable_cause = NULL WHERE role = 'owner'"
        )


def rebind_to_jellyfin(path) -> None:
    """Move the fixture's Other Files item to a Jellyfin item.

    0026 archives Other Files items; a test about another revision rebinds first so it
    does not also test that archive.
    """
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE media_items SET server_id = 'http://jellyfin.test', "
            "jellyfin_id = printf('%032x', rowid) WHERE server_id = 'editarr-manual'"
        )


def sidecar_state(media_dir):
    """Names, hashes, and modification times of every file beside the synthetic media."""
    return {
        entry.name: (hashlib.sha256(entry.read_bytes()).hexdigest(), entry.stat().st_mtime_ns)
        for entry in sorted(Path(media_dir).iterdir())
    }


def assert_assigned(path, original, columns, owner, *, source_revision, after_setup=False):
    """Legacy columns are unchanged and every activity row carries ``owner`` (None: awaiting).

    Account tables are compared too, unless owner setup has since created the owner.
    Policy rows a 0011 source already had are compared with the other original rows.
    Every account has a filter mode (0031), live unless it chose copy, and a copy-mode
    account has a copy.
    """
    if source_revision not in _ACCOUNT_MIGRATED and not after_setup:
        assert_preserved_after_account_migration(path, original, columns)
    with sqlite3.connect(path) as database:
        selected, actual, expected = {}, {}, {}
        for name, names in columns.items():
            if name in _ACCOUNT_TABLES and (
                after_setup or source_revision not in _ACCOUNT_MIGRATED
            ):
                continue
            if (after_setup and name in _SETUP_TABLES) or name == "user_filter_preferences":
                continue
            kept = [index for index, column in enumerate(names) if column not in _ASSIGNED]
            selected[name] = [names[index] for index in kept]
            rows = [tuple(row[index] for index in kept) for row in original[name]]
            expected[name] = comparable(name, selected[name], rows)
            actual[name] = legacy_rows(database, name, selected[name])
        expected = retire_publications(expected, actual, selected)
        for name in selected:
            assert actual[name] == expected[name], name
    revision = 1 if owner is not None else None
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT id, owner_id, original_author_id FROM saved_reviews ORDER BY id"
        ).fetchall() == [
            ("review-imported-002", owner, owner),
            ("review-legacy-001", owner, owner),
        ]
        assert database.execute(
            "SELECT id, initiator_id, initiator_permission_revision FROM scans ORDER BY id"
        ).fetchall() == [
            ("imported-scan-003", owner, revision),
            ("legacy-scan-001", owner, revision),
            ("queued-scan-002", owner, revision),
        ]
        assert database.execute(
            "SELECT id, scope, target_user_id, target_export_id, actor_id, policy_owner_id, "
            "original_author_id, policy_revision FROM media_item_exports ORDER BY id"
        ).fetchall() == [
            (1, "legacy", None, None, owner, owner, owner, None),
            (2, "legacy", None, None, owner, None, None, None),
        ]
        assert database.execute("SELECT default_mode FROM filter_settings").fetchall() == [
            ("live",)
        ]
        assert database.execute(
            "SELECT id, user_id, all_sources FROM media_access_scopes"
        ).fetchall() == [(1, None, 1)]
        stored = {
            row[0]: (row[1] or "live", *row[2:4])
            for row in original.get("user_filter_preferences", [])
        }
        users = [user for (user,) in database.execute("SELECT id FROM users ORDER BY id")]
        assert database.execute(
            "SELECT user_id, mode, revision, policy_revision FROM user_filter_preferences "
            "ORDER BY user_id"
        ).fetchall() == [(user, *stored.get(user, ("live", 0, 0))) for user in users]
        copied = sorted(user for user, (mode, *_) in stored.items() if mode == "copy")
        assert [
            user
            for (user,) in database.execute(
                "SELECT DISTINCT user_id FROM user_filter_copy_severities ORDER BY user_id"
            )
        ] == copied
        empty = ("user_filter_severity_overrides",) + (() if copied else PERSONAL_VALUE_TABLES)
        empty += () if source_revision == ACTIVITY else _POLICY_TABLES
        for table in empty:
            assert database.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,), table
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
