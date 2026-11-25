"""File-backed restores into empty disposable installations, and their recovery."""

import json
import shutil
import sqlite3
import subprocess
import sys
import tarfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.backup.activation import (
    GENERATION,
    HOLD,
    JOURNAL,
    RECOVERY,
    REPORT,
    activate_restore,
    recover_restore,
)
from scanner.backup.archive import BackupError, export_backup
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import (
    HAS_ACCOUNTS,
    HAS_IDENTITY,
    HAS_WORK,
    RestoreRefused,
)
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
from scanner.persistence.migrations.preflight import inspect_schema, require_supported_schema
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.persistence.repository.repository_community import (
    load_local_identity,
    pending_after_restore,
)
from scanner.runtime.maintenance import DatabaseMaintenance, MaintenanceUnavailable
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from scanner.security.community_key_recovery import prepare_recovery, recovery_source
from scanner.security.private_identity_files import IdentityHome
from scanner.settings.model_scan_settings import (
    ModelScanSettings,
    load_model_scan_settings,
    save_model_scan_settings,
)
from tests.community_identity_support import identity_home


def _owner(database, name):
    """A Jellyfin-linked owner, the only owner a backup restores."""
    database.execute(
        "INSERT INTO users (id, role, status, username, normalized_username, local_export_id, "
        "permission_revision, created_at, updated_at) VALUES (1, 'owner', 'active', ?, ?, ?, 1, "
        "'2026-09-27', '2026-09-27')",
        (name, name, "local-" + "0" * 32),
    )
    database.execute(
        "INSERT INTO user_provider_identities (user_id, server_id, provider_user_id, link_state, "
        "created_at, updated_at) VALUES (1, 'server', ?, 'verified', '2026-09-27', '2026-09-27')",
        ("1" * 32,),
    )


def _fixture(tmp_path, encrypted=False, *, audio_padding=None):
    """A source with an owner, a connection, a key, and an identity, and an empty target.

    The target is a freshly migrated installation, as onboarding finds it.
    """
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    migrate_fresh_database(target / "scanner.db", target)
    database = source / "scanner.db"
    migrate_fresh_database(database, source)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) VALUES (1, ?, ?)",
            ("source-key", "2026-09-27"),
        )
        _owner(connection, "source")
        connection.execute(
            "INSERT INTO api_keys (id, user_id, token_digest, created_at, token_value) "
            "VALUES (1, 1, 'source-digest', '2026-10-13', 'source-api-key')"
        )
    load_or_create_installation_identity(identity_home(source))
    if audio_padding is not None:
        save_model_scan_settings(source, ModelScanSettings(audio_padding_seconds=audio_padding))
    archive = tmp_path / "portable.backup"
    export_backup(
        source / "scanner.db", source, archive, passphrase="dummy-only" if encrypted else None
    )
    return archive, target


def _restore(archive, target, **options):
    """Preview and activate `archive` into `target`; returns the move disagreements."""
    with preview_restore(archive, target, **options) as preview:
        return activate_restore(preview, target / "scanner.db", target, confirmed=True)[
            "move_disagreements"
        ]


def _enroll_community(directory, contributor_id, service_url):
    identity = save_identity_registration(
        identity_home(directory),
        contributor_id=contributor_id,
        service_url=service_url,
        last_known_key_state="active",
    )
    with sqlite3.connect(directory / "scanner.db") as database:
        database.execute(
            "INSERT INTO community_connections "
            "(credential_ref, service_url, contributor_id, installation_id, key_id) "
            "VALUES ((SELECT min(credential_ref) FROM community_identities), "
            "?, ?, ?, ?)",
            (service_url, contributor_id, identity.installation_id, identity.key_id),
        )
    return identity


def test_backup_keeps_a_disconnected_owners_identity_and_detached_connection(tmp_path):
    archive, _ = _fixture(tmp_path)
    source = tmp_path / "source"
    service = "https://community.test"
    identity = _enroll_community(source, "retained-contributor", service)
    home = identity_home(source)
    before = {path.name: path.read_bytes() for path in home.root.iterdir() if path.is_file()}
    engine = create_engine(f"sqlite:///{source / 'scanner.db'}")
    try:
        with Session(engine) as db:
            client = CommunityClient(
                db, home, CommunityTransport(ServiceEndpoint(service, "community.test"))
            )
            assert client.disconnect()["connected"] is False
    finally:
        engine.dispose()
    export_backup(source / "scanner.db", source, archive)
    with tarfile.open(archive) as exported:
        assert exported.extractfile("secrets/community/current.key").read() == before["current.key"]
        manifest = exported.extractfile("secrets/community/identity.json").read()
        assert manifest == before["identity.json"]
        assert json.loads(manifest)["contributor_id"] == identity.contributor_id
        database = tmp_path / "exported.sqlite3"
        database.write_bytes(exported.extractfile("database.sqlite3").read())
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT contributor_id, installation_id, key_id FROM community_connections"
        ).fetchone() == (None, identity.installation_id, identity.key_id)


@pytest.mark.parametrize("encrypted", [False, True])
def test_restore_brings_back_everything_and_pauses_the_queue(tmp_path, encrypted):
    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional age transport not installed")
    archive, target = _fixture(tmp_path, encrypted, audio_padding=0.45)
    source = tmp_path / "source"
    source_identity = (identity_home(source).root / "identity.json").read_bytes()
    options = {"passphrase": "dummy-only"} if encrypted else {}
    with preview_restore(archive, target, **options) as preview:
        with pytest.raises(BackupError, match="explicit confirmation"):
            activate_restore(preview, target / "scanner.db", target)
        report = activate_restore(preview, target / "scanner.db", target, confirmed=True)
    assert load_model_scan_settings(target, default_audio_padding=0.1).audio_padding_seconds == 0.45
    assert report == {
        "version": 1,
        "move_disagreements": [],
        "backup_deployment": {"base_url": None, "port": None},
    }
    with sqlite3.connect(target / "scanner.db") as connection:
        assert connection.execute("SELECT username FROM users").fetchall() == [("source",)]
        assert connection.execute("SELECT api_key FROM jellyfin_settings").fetchone() == (
            "source-key",
        )
        assert connection.execute("SELECT token_digest, token_value FROM api_keys").fetchall() == [
            ("source-digest", "source-api-key")
        ]
        assert connection.execute("SELECT count(*) FROM auth_sessions").fetchone() == (0,)
        assert connection.execute(
            "SELECT actor_kind, actor_id, outcome, details_json FROM audit_events "
            "WHERE action = 'backup.restore'"
        ).fetchall() == [
            ("system", None, "succeeded", '{"move_disagreements":0,"stage":"restored"}')
        ]
    assert (identity_home(target).root / "identity.json").read_bytes() == source_identity
    assert json.loads((target / HOLD).read_text())["queue_paused"] is True
    assert json.loads((target / REPORT).read_text()) == report
    assert not (target / JOURNAL).exists()
    assert (target / RECOVERY / "before" / "database.sqlite3").is_file()
    assert inspect_schema(target / "scanner.db", target) == "0040_review_community_filters"


def test_restore_into_a_directory_without_a_database(tmp_path):
    archive, target = _fixture(tmp_path)
    (target / "scanner.db").unlink()
    _restore(archive, target)
    with sqlite3.connect(target / "scanner.db") as connection:
        assert connection.execute("SELECT username FROM users").fetchall() == [("source",)]
    inventory = json.loads((target / RECOVERY / "inventory.json").read_text())
    assert inventory["before"]["database.sqlite3"] is None


@pytest.mark.parametrize(
    "occupy,reason",
    [
        (
            "INSERT INTO users (id, role, status, username, normalized_username, "
            "local_export_id, permission_revision, created_at, updated_at) VALUES "
            "(1, 'owner', 'active', 'admin', 'admin', 'local-00000000000000000000000000000000', "
            "1, 'x', 'x')",
            HAS_ACCOUNTS,
        ),
        (
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) VALUES (1, 'k', 'x')",
            HAS_WORK,
        ),
        (
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, created_at, "
            "updated_at) VALUES ('item', 'server', 'media', 'Movie', 'Kept', 'x', 'x')",
            HAS_WORK,
        ),
        ("identity", HAS_IDENTITY),
    ],
)
@pytest.mark.parametrize("when", ["preflight", "activation"])
def test_a_non_empty_target_is_refused_before_any_change(tmp_path, occupy, reason, when):
    archive, target = _fixture(tmp_path)

    def fill():
        if occupy == "identity":
            load_or_create_installation_identity(IdentityHome(target, "c" * 32))
        else:
            with sqlite3.connect(target / "scanner.db") as database:
                database.execute(occupy)

    if when == "preflight":
        fill()
    before = {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }
    with pytest.raises(RestoreRefused) as refused:
        with preview_restore(archive, target) as preview:
            fill()
            before = {
                path.relative_to(target).as_posix(): path.read_bytes()
                for path in target.rglob("*")
                if path.is_file()
            }
            activate_restore(preview, target / "scanner.db", target, confirmed=True)
    assert refused.value.reason == reason
    assert {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    } == before
    assert not any((target / name).exists() for name in (JOURNAL, GENERATION, HOLD, RECOVERY))


def test_activation_keeps_community_registration_with_its_identity(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    service_url = "https://community.example"
    source_identity = _enroll_community(source, "source-contributor", service_url)
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    restored = load_or_create_installation_identity(identity_home(target))
    assert (restored.installation_id, restored.key_id) == (
        source_identity.installation_id,
        source_identity.key_id,
    )
    with sqlite3.connect(target / "scanner.db") as database:
        assert database.execute(
            "SELECT contributor_id, installation_id, key_id FROM community_connections"
        ).fetchall() == [
            ("source-contributor", source_identity.installation_id, source_identity.key_id)
        ]
    engine = create_engine(f"sqlite:///{target / 'scanner.db'}")
    try:
        with Session(engine) as database:
            assert load_local_identity(database, identity_home(target)).key_id == (
                source_identity.key_id
            )
    finally:
        engine.dispose()


def test_every_accounts_identity_and_pending_operation_restore(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    service_url = "https://community.example"
    owner = _enroll_community(source, "owner-contributor", service_url)
    peer_ref = "b" * 32
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(
            "INSERT INTO users (id, role, status, username, normalized_username, "
            "local_export_id, permission_revision, created_at, updated_at) VALUES "
            "(2, 'user', 'active', 'peer', 'peer', ?, 1, '2026-09-27', '2026-09-27')",
            ("local-" + "1" * 32,),
        )
        database.execute(
            "INSERT INTO community_identities (credential_ref, user_id, created_at) "
            "VALUES (?, 2, '2026-10-23')",
            (peer_ref,),
        )
    load_or_create_installation_identity(IdentityHome(source, peer_ref))
    peer = save_identity_registration(
        IdentityHome(source, peer_ref),
        contributor_id="peer-contributor",
        service_url=service_url,
        last_known_key_state="active",
    )
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(
            "INSERT INTO community_connections "
            "(credential_ref, service_url, contributor_id, installation_id, key_id) "
            "VALUES (?, ?, 'peer-contributor', ?, ?)",
            (peer_ref, service_url, peer.installation_id, peer.key_id),
        )
        database.execute(
            "INSERT INTO community_operations (operation_id, service_url, kind, state, "
            "contributor_id, installation_id, owner_id, actor_id, receipt, updated_at) "
            "VALUES ('peer-pending', ?, 'filter_vote', 'pending', 'peer-contributor', ?, 2, 2, "
            "'signed-receipt', '2026-10-23')",
            (service_url, peer.installation_id),
        )
    peer_files = {
        path.name: path.read_bytes()
        for path in IdentityHome(source, peer_ref).root.iterdir()
        if path.name in ("identity.json", "current.key", "pending.key")
    }
    export_backup(source / "scanner.db", source, archive)
    with tarfile.open(archive) as exported:
        assert f"secrets/community-identities/{peer_ref}/identity.json" in exported.getnames()
    _restore(archive, target)

    restored_owner = load_or_create_installation_identity(identity_home(target))
    assert restored_owner.key_id == owner.key_id
    restored_home = IdentityHome(target, peer_ref).root
    assert {name: (restored_home / name).read_bytes() for name in peer_files} == peer_files
    engine = create_engine(f"sqlite:///{target / 'scanner.db'}")
    try:
        with Session(engine) as database:
            pending = pending_after_restore(
                database,
                credential_ref=peer_ref,
                service_url=service_url,
                contributor_id="peer-contributor",
                installation_id=peer.installation_id,
                key_id=peer.key_id,
            )
            assert [(row.operation_id, row.owner_id) for row in pending] == [("peer-pending", 2)]
    finally:
        engine.dispose()


def test_activation_preserves_missing_old_key_for_pending_recovery(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    source_identity = _enroll_community(source, "source-contributor", "https://community.example")
    (identity_home(source).root / "current.key").unlink()
    pending = prepare_recovery(identity_home(source), "community.example", source_identity.key_id)
    export_backup(source / "scanner.db", source, archive)
    with preview_restore(archive, target) as preview:
        assert not (preview.stage / "secrets/community/current.key").exists()
        staged = json.loads((preview.stage / "secrets/community/identity.json").read_text())
        assert staged["recovery"]["candidatePrivateKey"]
        activate_restore(preview, target / "scanner.db", target, confirmed=True)
    restored = recovery_source(identity_home(target))
    assert (restored.installation_id, restored.key_id) == (
        source_identity.installation_id,
        source_identity.key_id,
    )
    assert restored.private_key is None
    assert restored.recovery["newKeyId"] == pending.recovery["newKeyId"]
    assert restored.recovery["candidatePrivateKey"] == pending.recovery["candidatePrivateKey"]


def test_activation_rejects_a_mismatched_community_registration(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    _enroll_community(source, "source-contributor", "https://community.example")
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute("UPDATE community_connections SET key_id = ?", ("sha256:" + "0" * 64,))
    export_backup(source / "scanner.db", source, archive)
    with pytest.raises(BackupError, match="does not match its identity"):
        _restore(archive, target)
    assert not any((target / name).exists() for name in (JOURNAL, GENERATION, HOLD))
    with sqlite3.connect(target / "scanner.db") as database:
        assert database.execute("SELECT count(*) FROM users").fetchone() == (0,)


def test_activation_does_not_replay_an_unmanaged_wal(tmp_path):
    archive, target = _fixture(tmp_path)
    connection = sqlite3.connect(target / "scanner.db")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE filter_settings SET updated_at = 'committed-wal'")
        connection.commit()
        with preview_restore(archive, target) as preview:
            with pytest.raises(BackupError, match="unmanaged database connection"):
                activate_restore(preview, target / "scanner.db", target, confirmed=True)
            assert not (target / JOURNAL).exists()
    finally:
        connection.close()
    _restore(archive, target)
    with sqlite3.connect(target / "scanner.db") as database:
        assert database.execute("SELECT updated_at FROM filter_settings").fetchone() != (
            "committed-wal",
        )
        assert database.execute("SELECT username FROM users").fetchall() == [("source",)]


def test_target_settings_files_stay_when_the_backup_has_none(tmp_path, monkeypatch):
    from scanner.persistence.migrations import preflight

    archive, target = _fixture(tmp_path)
    (target / "deployment.json").write_text('{"version":1,"base_url":"http://localhost:8000"}')
    (target / "deployment.json").chmod(0o600)
    _restore(archive, target)
    assert json.loads((target / "deployment.json").read_text())["base_url"] == (
        "http://localhost:8000"
    )
    monkeypatch.setattr(preflight, "database_location", lambda: (target / "scanner.db", target))
    with pytest.raises(RuntimeError, match="reconciliation"):
        require_supported_schema()


def test_revoked_identity_stays_revoked_pending_online_verification(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    path = identity_home(source).root / "identity.json"
    identity = json.loads(path.read_text())
    identity.update(
        {
            "contributor_id": "contributor-test",
            "service_url": "https://example.invalid",
            "last_known_key_state": "revoked",
        }
    )
    path.write_text(json.dumps(identity))
    path.chmod(0o600)
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    result = json.loads((identity_home(target).root / "identity.json").read_text())
    assert result["last_known_key_state"] == "revoked"
    assert json.loads((target / HOLD).read_text())["online_key_verification_required"]


def test_missing_local_mapping_is_reported_without_exposing_path(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    missing = tmp_path / "missing-mapping-canary"
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(
            "INSERT INTO path_mappings (remote_path, local_path, sort_order, "
            "created_at, updated_at) VALUES (?, ?, 0, '2026-09-27', '2026-09-27')",
            ("/remote", str(missing)),
        )
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    hold = (target / HOLD).read_text()
    assert json.loads(hold)["path_review_required"] is True
    assert json.loads(hold)["connection_review_required"] is True
    assert str(missing) not in hold


def test_restore_expires_sessions_and_neutralizes_worker_metadata(tmp_path):
    archive, target = _fixture(tmp_path)
    source = tmp_path / "source"
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(
            "INSERT INTO auth_sessions (user_id, token_digest, csrf_token_digest, "
            "csrf_token_ciphertext, created_at, last_used_at, absolute_expires_at, "
            "remember_me) VALUES (1, 'old-session', 'old-csrf', 'old-secret', "
            "'2026-09-27', '2026-09-27', '2027-09-27', 0)"
        )
        database.execute(
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
            "created_at, updated_at) VALUES ('item', 'server', 'media', 'Movie', 'Test', "
            "'2026-09-27', '2026-09-27')"
        )
        database.execute(
            "INSERT INTO scans (id, media_item_id, scan_number, source_path, "
            "resolved_local_path, status, scan_audio, scan_video, speed_up, qwen_model, "
            "qwen_summaries_per_segment, qwen_segment_join_gap_seconds, selection_revision, "
            "visual_checkpoint_complete, audio_extraction_checkpoint_complete, "
            "audio_checkpoint_complete, checkpoint_stage, checkpoint_state, "
            "worker_pid, worker_pgid, worker_token, created_at, updated_at) "
            "VALUES ('scan', 'item', 1, '/missing', '/missing', 'scanning', "
            "1, 0, 0, 'qwen3-vl-8b-instruct-4bit', 4, 3.0, 0, 0, 0, 0, "
            "'queued', '{}', 12345, 12345, 'stale-token', '2026-09-27', '2026-09-27')"
        )
        database.execute(
            "UPDATE scans SET worker_started_at = '2026-09-27 09:00:00', "
            "worker_attempt_started_at = '2026-09-27 10:00:00', "
            "worker_elapsed_seconds = 23, worker_heartbeat_at = '2026-09-27 10:00:07'"
        )
        database.execute(
            "INSERT INTO scan_queue (scan_id, sort_order, created_at, updated_at) "
            "VALUES ('scan', 0, '2026-09-27', '2026-09-27')"
        )
    export_backup(source / "scanner.db", source, archive)
    _restore(archive, target)
    with sqlite3.connect(target / "scanner.db") as database:
        assert database.execute("SELECT count(*) FROM auth_sessions").fetchone() == (0,)
        assert database.execute(
            "SELECT status, worker_pid, worker_pgid, worker_token FROM scans"
        ).fetchone() == ("orphaned", None, None, None)
        assert database.execute(
            "SELECT worker_elapsed_seconds, worker_attempt_started_at FROM scans"
        ).fetchone() == (30, None)
        assert database.execute("SELECT count(*) FROM scan_queue").fetchone() == (1,)
    assert (target / HOLD).exists()


def test_restore_refuses_active_worker_lease_without_changes(tmp_path):
    archive, target = _fixture(tmp_path)
    original = (target / "scanner.db").read_bytes()
    with preview_restore(archive, target) as preview:
        with DatabaseMaintenance(target).worker_lease():
            with pytest.raises(MaintenanceUnavailable):
                activate_restore(
                    preview, target / "scanner.db", target, confirmed=True, timeout=0.05
                )
    assert (target / "scanner.db").read_bytes() == original
    assert not (target / JOURNAL).exists()


_ACTIVATE = (
    "context = preview_restore(Path(sys.argv[1]), Path(sys.argv[2])); "
    "value = context.__enter__(); "
    "a.activate_restore(value, Path(sys.argv[2]) / 'scanner.db', Path(sys.argv[2]), "
    "confirmed=True)"
)


def _empty_again(target, *, database):
    assert not (target / GENERATION).exists()
    assert not (target / HOLD).exists()
    assert not (target / REPORT).exists()
    assert not (target / "secrets/community/identity.json").exists()
    if database:
        with sqlite3.connect(target / "scanner.db") as connection:
            assert connection.execute("SELECT count(*) FROM users").fetchone() == (0,)
        assert inspect_schema(target / "scanner.db", target) == "0040_review_community_filters"
    else:
        assert not (target / "scanner.db").exists()


def test_damaged_rollback_generation_is_not_applied(tmp_path):
    archive, target = _fixture(tmp_path)
    script = (
        "import os, sys; from pathlib import Path; "
        "from scanner.backup import activation as a; "
        "from scanner.backup.restore import preview_restore; "
        "original = a._record; "
        "a._record = lambda path, data: (original(path, data), os._exit(47)) "
        "if path.name == a.JOURNAL else original(path, data); " + _ACTIVATE
    )
    result = subprocess.run([sys.executable, "-c", script, str(archive), str(target)], check=False)
    assert result.returncode == 47
    (target / GENERATION / "before" / "database.sqlite3").write_bytes(b"tampered")
    with pytest.raises(BackupError, match="recovery generation is incomplete"):
        recover_restore(target / "scanner.db", target)
    assert (target / JOURNAL).exists()


@pytest.mark.parametrize("boundary", ["prepared", "temporary"])
def test_early_crash_cleans_only_its_own_generation_and_temp_files(tmp_path, boundary):
    archive, target = _fixture(tmp_path)
    script = (
        "import os, sys; from pathlib import Path; "
        "from scanner.backup import activation as a; "
        "from scanner.backup.restore import preview_restore; "
        "boundary = sys.argv[3]; original_record = a._record; "
        "original_replace = a.os.replace; "
        "a._record = lambda path, data: os._exit(51) "
        "if boundary == 'prepared' and path.name == a.JOURNAL "
        "else original_record(path, data); "
        "a.os.replace = lambda source, dest: os._exit(52) "
        "if boundary == 'temporary' and Path(source).name.startswith('.restore-scanner.db-') "
        "else original_replace(source, dest); " + _ACTIVATE
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(archive), str(target), boundary], check=False
    )
    assert result.returncode == (51 if boundary == "prepared" else 52)
    if boundary == "prepared":
        assert not (target / JOURNAL).exists()
        assert recover_restore(target / "scanner.db", target) is False
    else:
        assert (target / JOURNAL).exists()
        assert recover_restore(target / "scanner.db", target)
        assert not list(target.glob(".restore-scanner.db-*"))
    _empty_again(target, database=True)
