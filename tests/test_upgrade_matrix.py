"""MU-45: every source revision upgrades to head with its activity, credentials, and backup.

One populated fixture per source revision: mu03-activity-fixture-2 and
mu06-community-fixture-1 through 0017, then the 0017 database upgraded to each later
revision, plus the state each revision introduced (profiles, avatars, Jellyfin sync,
review repairs, retained personal exports, regeneration jobs, link transitions and
export moves), seeded at every source that holds it. Each run checks the promises
together: every row and every shared column value kept except the permitted
transformations, the representative values literally before and after, one owner,
disabled with its password deleted because it has no Jellyfin link (0030), its API
key kept, all activity credited to the owner, identity and sidecar bytes unchanged,
and the verified pre-upgrade generation recovering the exact source database.

Released revisions (unversioned through 0009) also revoke every browser session, so
their old cookie fails. 0010-0024 are unreleased development revisions; their upgrade
need not revoke sessions (ruling wave16-upgrade-session-revocation), so the matrix
asserts neither outcome for them.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scanner.backup.account_family import OWNER_CREDENTIAL
from scanner.backup.activation import activate_restore
from scanner.backup.archive import export_backup
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import authenticate_api_key, seal_plaintext_api_keys
from scanner.security.auth_crypto import encrypt_csrf_token, load_csrf_encryption_key
from scanner.security.auth_repository_sessions import (
    _SESSION_DIGEST_PREFIX,
    _digest_opaque_token,
    _new_opaque_token,
    revoke_sessions_with_other_csrf_key_id,
)
from tests import upgrade_matrix_support as state
from tests.activity_migration_support import _upgrade, link_owner, sidecar_state
from tests.community_identity_migration_support import no_network as no_network
from tests.explicit_filter_modes_support import (
    FILTER_MODE_TABLES,
    drop_filter_migration_additions,
    legacy_instance_severities,
)
from tests.test_account_migration import _cookie, _sign_in, _snapshot
from tests.test_account_migration import http as http
from tests.test_api_key_migration import DIGEST, KEY
from tests.test_jellyfin_sync_migration import SOURCES as PROFILE_SOURCES
from tests.test_jellyfin_sync_migration import _built as _profiles_built

LATER = [
    "0018_jellyfin_account_sync",
    "0019_review_policy_evidence",
    "0020_review_scan_repairs",
    "0021_export_snapshots",
    "0022_export_regenerations",
    "0023_account_link_transitions",
    "0024_account_dispositions",
    "0025_archive_arr_sources",
    "0026_archive_manual_files",
    "0027_jellyfin_owner",
    "0028_jellyfin_admission",
    "0029_jellyfin_user_blocks",
    "0030_remove_passwords",
    "0031_explicit_filter_modes",
    "0032_manual_filter_tracking",
    "0033_scan_worker_timing",
    "0034_scan_read_indexes",
    "0035_stable_media_identity",
    "0036_sampled_media_identity",
    "0037_community_master_sets",
    "0038_retire_receipt_capabilities",
    "0039_review_community_sources",
    "0040_review_community_filters",
]
SOURCES = [*PROFILE_SOURCES, *LATER]
ACCOUNTS = "0010_user_accounts"
RELEASED = SOURCES[: SOURCES.index(ACCOUNTS)]
_TIME = "%Y-%m-%d %H:%M:%S.%f"


def test_the_matrix_covers_every_revision_below_head():
    versions = Path(adoption.__file__).parent / "versions"
    chain = [None, *(path.stem for path in sorted(versions.glob("0*.py")))]
    assert chain[-1] == adoption.HEAD_REVISION == "0041_mcf_1_1_1"
    assert SOURCES == chain[:-1]


def _built(tmp_path, revision):
    path = _profiles_built(tmp_path, PROFILE_SOURCES[-1] if revision in LATER else revision)
    if revision in LATER:
        _upgrade(path, revision)
    state.seed(path, revision)
    return path


def _live_session(path):
    """Add one unexpired, unrevoked browser session for the owner; return its cookie token.

    The CSRF ciphertext uses the data directory's real key, so startup's key check keeps it.
    """
    token = _new_opaque_token()
    digest = _digest_opaque_token(token, prefix=_SESSION_DIGEST_PREFIX)
    key = load_csrf_encryption_key(path.parent)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(path) as database:
        columns = {row[1] for row in database.execute("PRAGMA table_info(auth_sessions)")}
        owner = "user_id" if "user_id" in columns else "admin_user_id"
        values = {
            owner: 1,
            "token_digest": digest,
            "csrf_token_digest": "csrf",
            "csrf_token_ciphertext": encrypt_csrf_token(_new_opaque_token(), digest, key),
            "created_at": now.strftime(_TIME),
            "last_used_at": now.strftime(_TIME),
            "absolute_expires_at": (now + timedelta(days=1)).strftime(_TIME),
            "idle_expires_at": (now + timedelta(days=1)).strftime(_TIME),
            "remember_me": 1,
        }
        values = {name: value for name, value in values.items() if name in columns}
        database.execute(
            f"INSERT INTO auth_sessions ({', '.join(values)}) "
            f"VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )
    return token


def _live_sessions(path):
    with sqlite3.connect(path) as database:
        query = "SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL"
        return database.execute(query).fetchone()[0]


def _owner(path):
    with sqlite3.connect(path) as database:
        return database.execute(
            "SELECT role, status, disable_cause, username, display_name FROM users "
            "LEFT JOIN user_profiles ON user_id = id WHERE id = 1"
        ).fetchone()


def _owner_name(revision):
    """0030 keeps a profile display name and otherwise moves the owner's username there."""
    seeded = revision is not None and revision >= "0017_user_profiles"
    return "Fixture Owner" if seeded else "fixture-admin"


def _files(root):
    """Content hashes of every identity file under secrets/, wherever the upgrade put it."""
    secrets = root / "secrets"
    return sorted(
        hashlib.sha256(entry.read_bytes()).hexdigest()
        for entry in secrets.rglob("*")
        if entry.is_file()
    )


def _counts(rows):
    return {state.RENAMED.get(name, name): len(values) for name, values in rows.items()}


def _activity(path):
    with sqlite3.connect(path) as database:
        return {
            "owners": database.execute("SELECT id FROM users WHERE role = 'owner'").fetchall(),
            "reviews": database.execute(
                "SELECT DISTINCT owner_id, original_author_id FROM saved_reviews"
            ).fetchall(),
            "scans": database.execute("SELECT DISTINCT initiator_id FROM scans").fetchall(),
            "exports": database.execute(
                "SELECT id, scope, target_user_id, actor_id, policy_owner_id, original_author_id "
                "FROM media_item_exports ORDER BY id"
            ).fetchall(),
            "identities": database.execute(
                "SELECT DISTINCT user_id FROM community_identities"
            ).fetchall(),
            "operations": database.execute(
                "SELECT DISTINCT owner_id FROM community_operations"
            ).fetchall(),
            "key": database.execute("SELECT user_id, token_digest FROM api_keys").fetchall(),
        }


@pytest.mark.parametrize("revision", SOURCES)
def test_every_source_revision_upgrades_with_activity_credentials_and_backup(
    tmp_path, http, revision
):
    path = _built(tmp_path, revision)
    original, _ = _snapshot(path)
    source = state.content(path)
    held = state.expected(revision, upgraded=False)
    assert state.read(path, held) == held
    identity = _files(path.parent)
    sidecars = sidecar_state(tmp_path / "media")
    keyed = "api_keys" in original
    live = _live_sessions(path)

    generation = adoption.upgrade_existing_database(path, path.parent)

    assert (generation / "snapshot.sqlite3").is_file()
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION
    assert adoption.upgrade_existing_database(path, path.parent) is None
    upgraded, _ = _snapshot(path)
    drop_filter_migration_additions(upgraded, original)
    before, after = _counts(original), _counts(upgraded)
    assert {name: after.get(name) for name in before} == before
    content = state.content(path)
    columns, rows = content["mcf_severity_defaults"]
    content["mcf_severity_defaults"] = (columns, legacy_instance_severities(rows))
    changed = state.unchanged(source, content)
    assert set(changed) <= set(FILTER_MODE_TABLES)
    kept = state.expected(revision, upgraded=True)
    assert state.read(path, kept) == kept
    if revision in RELEASED:
        assert (live, _live_sessions(path)) == (2, 0)
    assert _owner(path) == ("owner", "disabled", "unlinked", None, _owner_name(revision))
    assert _files(path.parent) == identity
    assert sidecar_state(tmp_path / "media") == sidecars
    assert _activity(path) == {
        "owners": [(1,)],
        "reviews": [(1, 1)],
        "scans": [(1,)],
        "exports": [(1, "legacy", None, 1, 1, 1), (2, "legacy", None, 1, None, None)]
        + ([(3, "personal", 1, 1, 1, 1)] if state.PERSONAL in held else []),
        "identities": [(1,)],
        "operations": [(1,)] if original.get("community_operations") else [],
        "key": [(1, DIGEST)] if keyed else [],
    }

    client, factory = http(path)
    assert _sign_in(client, "fixture-admin", "any password").status_code == 404
    if keyed:
        with factory() as db:
            seal_plaintext_api_keys(db, load_api_key_wrap_key(path.parent))
            assert authenticate_api_key(db, KEY).user_id == 1
    refused = client.get("/api/protected", headers={"X-Api-Key": KEY}).json()["code"]
    assert refused == ("account_disabled" if keyed else "invalid_api_key")

    recovered = adoption.export_upgrade_recovery(path, path.parent, tmp_path / "recovered.db")
    assert adoption._inspect_database(recovered) == revision
    assert _snapshot(recovered)[0] == original


def _restored_scan(row):
    """Activation orphans a queued scan, because its worker belonged to the source."""
    return (*row[:-1], "orphaned") if row[-1] == "queued" else row


@pytest.mark.parametrize("revision", SOURCES)
def test_every_source_revision_backup_is_refused_until_the_source_upgrades(tmp_path, revision):
    """Restore accepts only head backups whose owner has a Jellyfin link."""
    path = _built(tmp_path, revision)
    original, _ = _snapshot(path)
    archive = tmp_path / "backups" / "before-upgrade.backup"
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    migrate_fresh_database(target / "scanner.db", target)
    empty = _snapshot(target / "scanner.db")[0]

    manifest = export_backup(path, path.parent, archive)

    assert manifest["schema_revision"] == (revision or "unversioned-v1")
    assert adoption._inspect_database(path) == revision
    assert _snapshot(path)[0] == original
    with pytest.raises(RestoreRefused, match=f"schema {revision or 'unversioned-v1'}\\."):
        with preview_restore(archive, target):
            pass
    assert _snapshot(target / "scanner.db")[0] == empty

    adoption.upgrade_existing_database(path, path.parent)
    export_backup(path, path.parent, tmp_path / "backups" / "unlinked-owner.backup")
    with pytest.raises(RestoreRefused) as refused:
        with preview_restore(tmp_path / "backups" / "unlinked-owner.backup", target):
            pass
    assert refused.value.reason == OWNER_CREDENTIAL
    link_owner(path)
    export_backup(path, path.parent, tmp_path / "backups" / "after-upgrade.backup")
    with preview_restore(tmp_path / "backups" / "after-upgrade.backup", target) as preview:
        assert preview.plan["source_schema"] == adoption.HEAD_REVISION
        activate_restore(preview, target / "scanner.db", target, confirmed=True)
    restored = target / "scanner.db"
    assert adoption._inspect_database(restored) == adoption.HEAD_REVISION
    kept = state.expected(revision, upgraded=True)
    assert state.read(restored, kept) == {
        query: [_restored_scan(row) if "FROM scans" in query else row for row in rows]
        for query, rows in kept.items()
    }
    assert _owner(restored) == ("owner", "active", None, None, _owner_name(revision))
    activity = _activity(restored)
    assert (activity["owners"], activity["reviews"], activity["scans"]) == (
        [(1,)],
        [(1, 1)],
        [(1,)],
    )


@pytest.mark.parametrize("revision", RELEASED)
def test_every_released_revision_upgrade_refuses_the_old_session_cookie(tmp_path, http, revision):
    path = _built(tmp_path, revision)
    cookie = _live_session(path)
    with sqlite3.connect(path) as database:
        assert database.execute(
            "SELECT count(*) FROM auth_sessions WHERE token_digest = ? AND revoked_at IS NULL",
            (_digest_opaque_token(cookie, prefix=_SESSION_DIGEST_PREFIX),),
        ).fetchone() == (1,)

    assert adoption.upgrade_existing_database(path, path.parent).is_dir()

    client, factory = http(path)
    with factory() as db:
        revoke_sessions_with_other_csrf_key_id(db, load_csrf_encryption_key(path.parent).key_id)
    response = client.get("/api/protected", headers=_cookie(cookie))
    assert (response.status_code, response.json().get("code")) == (
        401,
        "authentication_required",
    )
