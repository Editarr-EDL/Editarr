"""Portable backup tests use only isolated, disposable files."""

import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from scanner.backup.age_transport import AgeUnavailable, run_age, validate_age_header
from scanner.backup.archive import (
    BackupError,
    BackupPublicationUncertain,
    export_backup,
    validate_plain_archive,
)
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.runtime.maintenance import DatabaseMaintenance, MaintenanceUnavailable
from scanner.security.auth_crypto import load_csrf_encryption_key
from scanner.security.community_identity import load_or_create_installation_identity
from scanner.settings.deployment_config import DeploymentStore
from scanner.settings.diagnostics import DiagnosticsPreferences, save_diagnostics_preferences
from tests.community_identity_support import identity_home


@pytest.mark.parametrize("encrypted", [False, True])
def test_export_keeps_credentials_and_identity_but_not_runtime_artifacts(tmp_path, encrypted):
    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional age and Expect transport not installed")
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) "
            "VALUES (1, 'dummy-secret-canary', '2026-09-27')"
        )
        connection.execute(
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
            "created_at, updated_at) VALUES "
            "('item1', 'server1', 'media1', 'Movie', 'Example', '2026-09-27', '2026-09-27')"
        )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO api_keys (id, token_digest, created_at, token_value) VALUES (1, ?, ?, ?)",
            ("a" * 64, "2026-10-13", "b" * 64),
        )
    identity = load_or_create_installation_identity(identity_home(data_dir))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO community_connections "
            "(credential_ref, service_url, contributor_id, installation_id, key_id) "
            "VALUES ((SELECT min(credential_ref) FROM community_identities), "
            "?, 'contributor-1', ?, ?)",
            ("https://community.example", identity.installation_id, identity.key_id),
        )
        connection.execute(
            "INSERT INTO community_operations (operation_id, service_url, kind, state, "
            "contributor_id, installation_id, updated_at) "
            "VALUES ('retry-1', 'https://community.example', 'filter_vote', 'pending', "
            "'contributor-1', ?, '2026-09-27')",
            (identity.installation_id,),
        )
    (data_dir / "deployment.json").write_text('{"base_url":"http://example.invalid"}')
    (data_dir / "deployment.json").chmod(0o600)
    (data_dir / "cache").mkdir()
    (data_dir / "cache" / "excluded-canary").write_text("cache-canary")
    target = tmp_path / "output" / "backup.tar"
    secret = "dummy-only-test-passphrase" if encrypted else None
    manifest = export_backup(database, data_dir, target, passphrase=secret)
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    if encrypted:
        validate_age_header(target)
        outer = target.read_bytes()
        assert b"dummy-secret-canary" not in outer
        assert b"secrets/community" not in outer
        assert b"dummy-only-test-passphrase" not in outer
        decoded = tmp_path / "decoded.tar"
        run_age(target, decoded, secret, decrypt=True)
        assert validate_plain_archive(decoded, protection="age-scrypt") == manifest
        target = decoded
    else:
        assert validate_plain_archive(target) == manifest
    assert manifest["kind"] == "full"
    assert manifest["identity_included"] is True
    assert manifest["identity_key_id"] == identity.key_id
    assert manifest["installation_id"] == identity.installation_id
    assert manifest["portability"]["media_sidecars"] == "excluded"
    assert "community-signing-key" in manifest["included_secret_types"]
    assert manifest["protection"] == ("age-scrypt" if encrypted else "plain")
    with tarfile.open(target, "r:") as archive:
        assert "cache/excluded-canary" not in archive.getnames()
        assert "secrets/community/current.key" in archive.getnames()
        assert archive.extractfile("deployment.json").read() == (
            b'{"base_url":"http://example.invalid"}'
        )
        assert (
            identity.key_id.encode()
            in archive.extractfile("secrets/community/identity.json").read()
        )
        exported_db = tmp_path / "exported.db"
        exported_db.write_bytes(archive.extractfile("database.sqlite3").read())
    with sqlite3.connect(exported_db) as connection:
        assert connection.execute("SELECT token_digest, token_value FROM api_keys").fetchone() == (
            "a" * 64,
            "b" * 64,
        )
        assert connection.execute("SELECT api_key FROM jellyfin_settings").fetchone() == (
            "dummy-secret-canary",
        )
        assert connection.execute("SELECT count(*) FROM media_items").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM community_connections").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM community_operations").fetchone() == (1,)
    assert b"dummy-secret-canary" not in repr(manifest).encode()


def test_invalid_encryption_request_preserves_existing_export(tmp_path):
    destination = tmp_path / "backup.tar"
    destination.write_bytes(b"previous")
    with pytest.raises(BackupError, match="nonempty passphrase"):
        export_backup(tmp_path / "missing", tmp_path, destination, passphrase="")
    assert destination.read_bytes() == b"previous"


def test_encryption_failure_preserves_prior_archive_and_cleans_staging(tmp_path, monkeypatch):
    from scanner.backup import archive

    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.tar"
    export_backup(database, data_dir, destination)
    original = destination.read_bytes()

    def fail_age(*_args):
        raise AgeUnavailable("Simulated age failure")

    monkeypatch.setattr(archive, "run_age", fail_age)
    with pytest.raises(AgeUnavailable, match="Simulated age failure"):
        export_backup(database, data_dir, destination, passphrase="dummy-only")
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".editarr-backup-*"))


@pytest.mark.parametrize("failure_point", ["write", "publish"])
def test_disk_write_or_publish_failure_keeps_previous_backup(tmp_path, monkeypatch, failure_point):
    from scanner.backup import archive

    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.tar"
    export_backup(database, data_dir, destination)
    original = destination.read_bytes()
    if failure_point == "write":

        def fail_write(path, _manifest, _members):
            path.write_bytes(b"partial")
            raise OSError("Simulated disk exhaustion")

        monkeypatch.setattr(archive, "_write_archive", fail_write)
    else:
        original_replace = archive.os.replace

        def fail_publish(source, target):
            if target == destination:
                raise OSError("Simulated publication failure")
            return original_replace(source, target)

        monkeypatch.setattr(archive.os, "replace", fail_publish)
    with pytest.raises(OSError, match="Simulated"):
        export_backup(database, data_dir, destination)
    assert destination.read_bytes() == original
    assert not list(tmp_path.glob(".editarr-backup-*"))


def test_directory_sync_failure_reports_replaced_output_as_uncertain(tmp_path, monkeypatch):
    from scanner.backup import archive

    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.tar"
    export_backup(database, data_dir, destination)
    previous = destination.read_bytes()

    def fail_sync(_directory):
        raise OSError("Simulated directory sync failure")

    monkeypatch.setattr(archive, "_sync_directory", fail_sync)
    with pytest.raises(BackupPublicationUncertain, match="could not be confirmed"):
        export_backup(database, data_dir, destination)
    assert destination.read_bytes() != previous
    assert validate_plain_archive(destination)["kind"] == "full"
    assert not list(tmp_path.glob(".editarr-backup-*"))


@pytest.mark.parametrize("crash_point", ["write", "directory-sync"])
def test_interrupted_export_recovers_private_stage(tmp_path, crash_point):
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.tar"
    export_backup(database, data_dir, destination)
    previous = destination.read_bytes()
    script = (
        "import os, sys; from pathlib import Path; "
        "from scanner.backup import archive; "
        "writer = archive._write_archive; "
        "archive._write_archive = lambda *args: (writer(*args), os._exit(47)) "
        "if sys.argv[1] == 'write' else writer(*args); "
        "archive._sync_directory = lambda *args: os._exit(48) "
        "if sys.argv[1] == 'directory-sync' else None; "
        "archive.export_backup(*(Path(arg) for arg in sys.argv[2:]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, crash_point, str(database), str(data_dir), str(destination)],
        check=False,
    )
    assert result.returncode == (47 if crash_point == "write" else 48)
    if crash_point == "write":
        assert destination.read_bytes() == previous
    else:
        assert destination.read_bytes() != previous
        assert validate_plain_archive(destination)["kind"] == "full"
    stages = list(tmp_path.glob(".editarr-backup-*"))
    assert len(stages) == 1
    if crash_point == "write":
        assert (stages[0] / "archive.tar").is_file()
    assert export_backup(database, data_dir, destination)["kind"] == "full"
    assert validate_plain_archive(destination)["kind"] == "full"
    assert not list(tmp_path.glob(".editarr-backup-*"))


@pytest.mark.parametrize("damage", ["wrong-passphrase", "altered", "truncated"])
def test_age_rejects_wrong_or_damaged_ciphertext(tmp_path, damage):
    if not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional age and Expect transport not installed")
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.age"
    export_backup(database, data_dir, destination, passphrase="dummy-only")
    if damage == "wrong-passphrase":
        passphrase = "incorrect-dummy"
    else:
        passphrase = "dummy-only"
        with destination.open("r+b") as stream:
            if damage == "truncated":
                stream.truncate(destination.stat().st_size - 8)
            else:
                stream.seek(-1, 2)
                stream.write(bytes([stream.read(1)[0] ^ 1]))
    plaintext = tmp_path / "partial.tar"
    with pytest.raises(AgeUnavailable):
        run_age(destination, plaintext, passphrase, decrypt=True)
    assert not plaintext.exists()


def test_age_header_rejects_unapproved_work_factor(tmp_path):
    header = tmp_path / "header.age"
    header.write_bytes(b"age-encryption.org/v1\n-> scrypt AAAAAAAAAAAAAAAAAAAAAA 19\n")
    with pytest.raises(AgeUnavailable, match="work factor"):
        validate_age_header(header)


def test_plain_export_needs_no_age_or_expect(tmp_path, monkeypatch):
    from scanner.backup import age_transport

    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    monkeypatch.setattr(age_transport.shutil, "which", lambda _name: None)
    destination = tmp_path / "backup.tar"
    assert export_backup(database, data_dir, destination)["protection"] == "plain"
    assert validate_plain_archive(destination)["protection"] == "plain"
    with pytest.raises(AgeUnavailable, match="unavailable"):
        export_backup(database, data_dir, destination, passphrase="dummy-only")
    assert validate_plain_archive(destination)["protection"] == "plain"


def test_age_passphrase_is_sent_only_to_helper_stdin(tmp_path, monkeypatch):
    from scanner.backup import age_transport

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(age_transport.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(age_transport.subprocess, "run", fake_run)
    secret = "only-for-the-test-$-\\-canary"
    age_transport.run_age(tmp_path / "source", tmp_path / "output", secret)
    args, kwargs = calls[0]
    assert secret not in repr(args)
    assert kwargs["input"] == secret.encode()
    assert "env" not in kwargs
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_frozen_age_missing_helper_fails_without_host_fallback(tmp_path, monkeypatch):
    from scanner.backup import age_transport

    monkeypatch.setattr(age_transport, "_native_helper", lambda: tmp_path / "missing")
    with pytest.raises(AgeUnavailable, match="unavailable"):
        run_age(tmp_path / "source", tmp_path / "output", "dummy-only")
    assert not (tmp_path / "output").exists()


def test_native_age_helper_round_trip_and_rejection(tmp_path, monkeypatch):
    from scanner.backup import age_transport
    from scripts.build_release import _build_backup_age

    if not shutil.which("go"):
        pytest.skip("Go toolchain not installed")
    helper = _build_backup_age(tmp_path, "macos-arm64" if os.name == "posix" else "windows-x64")
    monkeypatch.setattr(age_transport, "_native_helper", lambda: helper)
    monkeypatch.setattr(age_transport.shutil, "which", lambda _name: None)
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.age"
    manifest = export_backup(database, data_dir, destination, passphrase="dummy-only")
    validate_age_header(destination)
    plaintext = tmp_path / "plain.tar"
    run_age(destination, plaintext, "dummy-only", decrypt=True)
    assert validate_plain_archive(plaintext, protection="age-scrypt") == manifest
    rejected = tmp_path / "rejected.tar"
    with pytest.raises(AgeUnavailable):
        run_age(destination, rejected, "wrong-passphrase", decrypt=True)
    assert not rejected.exists()
    with destination.open("r+b") as stream:
        stream.seek(-1, 2)
        last_byte = stream.read(1)
        stream.seek(-1, 2)
        stream.write(bytes([last_byte[0] ^ 1]))
    with pytest.raises(AgeUnavailable):
        run_age(destination, rejected, "dummy-only", decrypt=True)
    assert not rejected.exists()
    with destination.open("r+b") as stream:
        stream.truncate(destination.stat().st_size - 8)
    with pytest.raises(AgeUnavailable):
        run_age(destination, rejected, "dummy-only", decrypt=True)
    assert not rejected.exists()


def test_external_writers_do_not_change_generation_during_maintenance(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    operations = (
        lambda: DeploymentStore(data_dir).update(base_url="http://localhost:9000"),
        lambda: load_or_create_installation_identity(identity_home(data_dir)),
        lambda: save_diagnostics_preferences(data_dir, DiagnosticsPreferences(True)),
        lambda: load_csrf_encryption_key(data_dir, environment={}),
    )
    with DatabaseMaintenance(data_dir).exclusive():
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(operation) for operation in operations]
            for future in futures:
                with pytest.raises(MaintenanceUnavailable):
                    future.result(timeout=5)
        assert not (data_dir / "deployment.json").exists()
        assert not (data_dir / "diagnostics.json").exists()
        assert not (identity_home(data_dir).root / "identity.json").exists()
        assert not (data_dir / "secrets/auth-csrf.key").exists()


def test_rejects_unexpected_tar_entry(tmp_path):
    path = tmp_path / "malicious.tar"
    with tarfile.open(path, "w") as archive:
        manifest = json.dumps(
            {
                "format": "editarr-backup-1",
                "kind": "full",
                "protection": "plain",
                "schema_revision": "0002_scan_provenance",
                "identity_included": False,
                "identity_key_id": None,
                "omitted_environment": [],
                "included_secret_types": [],
                "files": {"database.sqlite3": {"size": 1, "sha256": "x"}},
            }
        ).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
        info = tarfile.TarInfo("../outside")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(BackupError, match="Unexpected backup member"):
        validate_plain_archive(path)


def test_rejects_appended_archive_bytes(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.tar"
    export_backup(database, data_dir, destination)
    with destination.open("ab") as handle:
        handle.write(b"extra")
    with pytest.raises(BackupError, match="trailing data"):
        validate_plain_archive(destination)


@pytest.mark.parametrize("damage", ["altered", "truncated"])
def test_rejects_corrupt_archives(tmp_path, damage):
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.tar"
    export_backup(database, data_dir, destination)
    if damage == "altered":
        with destination.open("r+b") as handle:
            handle.seek(2048)
            current = handle.read(1)
            handle.seek(2048)
            handle.write(bytes([current[0] ^ 1]))
    else:
        with destination.open("r+b") as handle:
            handle.truncate(destination.stat().st_size // 2)
    with pytest.raises((BackupError, tarfile.TarError)):
        validate_plain_archive(destination)


def test_rejects_manifest_authorized_unexpected_file(tmp_path):
    path = tmp_path / "malicious.tar"
    manifest = {
        "format": "editarr-backup-1",
        "kind": "full",
        "protection": "plain",
        "schema_revision": "0002_scan_provenance",
        "identity_included": False,
        "identity_key_id": None,
        "omitted_environment": [],
        "included_secret_types": [],
        "files": {"database.sqlite3": {}, "cache/unexpected": {}},
    }
    with tarfile.open(path, "w") as archive:
        encoded = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(encoded)
        archive.addfile(info, io.BytesIO(encoded))
    with pytest.raises(BackupError, match="Invalid backup inventory"):
        validate_plain_archive(path)


def test_external_csrf_key_omission_is_explicit(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    secrets = data_dir / "secrets"
    secrets.mkdir(mode=0o700)
    (secrets / "auth-csrf.key").write_bytes(b"dummy-file-key")
    (secrets / "auth-csrf.key").chmod(0o600)
    monkeypatch.setenv("EDITARR_AUTH_CSRF_ENCRYPTION_KEY", "dummy-environment-key")
    destination = tmp_path / "backup.tar"
    manifest = export_backup(database, data_dir, destination)
    assert manifest["omitted_environment"] == ["EDITARR_AUTH_CSRF_ENCRYPTION_KEY"]
    assert "secrets/auth-csrf.key" not in manifest["files"]
    assert b"dummy-environment-key" not in destination.read_bytes()


def test_committed_wal_record_and_failed_generation_leave_previous_export(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    destination = tmp_path / "backup.tar"
    export_backup(database, data_dir, destination)
    original = destination.read_bytes()
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute(
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) "
            "VALUES (1, 'wal-canary', '2026-09-27')"
        )
        writer.commit()
        manifest = export_backup(database, data_dir, destination)
        assert manifest["files"]["database.sqlite3"]["sha256"]
        assert destination.read_bytes() != original
        with tarfile.open(destination, "r:") as archive:
            recovered = tmp_path / "recovered.db"
            recovered.write_bytes(archive.extractfile("database.sqlite3").read())
        with sqlite3.connect(recovered) as connection:
            assert connection.execute(
                "SELECT api_key FROM jellyfin_settings WHERE id = 1"
            ).fetchone() == ("wal-canary",)
        original = destination.read_bytes()
    finally:
        writer.close()
    from scanner.backup import archive

    capture = archive.capture_external_files
    calls = 0

    def changed(_data_dir, database):
        nonlocal calls
        calls += 1
        files, omitted, key_id = capture(_data_dir, database)
        if calls == 2:
            files["deployment.json"] = b"new-generation"
        return files, omitted, key_id

    monkeypatch.setattr(archive, "capture_external_files", changed)
    with pytest.raises(BackupError, match="External settings changed"):
        export_backup(database, data_dir, destination)
    assert destination.read_bytes() == original
