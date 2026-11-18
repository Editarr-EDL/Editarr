"""Restore preflight tests use disposable source and target installations only."""

import hashlib
import io
import json
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile

import pytest
from pydantic import TypeAdapter

from scanner.backup.age_transport import AgeUnavailable
from scanner.backup.archive import BackupError, export_backup
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security.community_identity import load_or_create_installation_identity
from tests.activity_migration_support import _upgrade
from tests.community_identity_support import identity_home, only_identity_ref
from tests.community_schema_fixture import create_unversioned_fixture


def _fixture(tmp_path, *, encrypted=False):
    """A source full of work and identity, and an empty target installation."""
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    source_db = source / "scanner.db"
    target_db = target / "scanner.db"
    migrate_fresh_database(source_db, source)
    migrate_fresh_database(target_db, target)
    with sqlite3.connect(source_db) as database:
        database.execute(
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) VALUES (?, ?, ?)",
            (1, "private-canary", "2026-09-27"),
        )
        from scanner.modeling.media_identity import (
            MediaIdentity,
            ProviderIdentifier,
            normalize_manual,
        )

        identity_adapter = TypeAdapter(MediaIdentity)

        identity = normalize_manual(
            "movie", (ProviderIdentifier("tmdb", "movie", "501", "user_confirmed"),), confirmed=True
        )
        database.execute(
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, "
            "created_at, updated_at, confirmed_identity_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "media-fixture",
                "http://jellyfin.test",
                "a" * 32,
                "Video",
                "Private.Movie",
                "2026-10-02",
                "2026-10-02",
                identity_adapter.dump_json(identity).decode(),
            ),
        )
    source_identity = load_or_create_installation_identity(identity_home(source))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from scanner.db import (
        AudioTrackSelectionRow,
        CommunityOperationRow,
        CommunityPublicationRow,
        ScanRow,
    )
    from scanner.modeling.model_audio_tracks import AudioSelection
    from scanner.pipeline.audio_tracks import AudioTrack, AudioTrackSelection

    selected = AudioSelection.from_selection(
        AudioTrackSelection(
            "a" * 64, AudioTrack(1, "eng", "Main", "aac", 2, 1, 0.0, 10.0, "1/48000")
        )
    ).model_dump_json()
    engine = create_engine(f"sqlite:///{source_db}")
    with Session(engine) as database:
        database.add(
            ScanRow(
                id="audio-fixture",
                media_item_id="media-fixture",
                scan_number=1,
                source_path="/private/movie.mkv",
                resolved_local_path="/private/movie.mkv",
                requested_audio_selection_json=selected,
                analyzed_audio_selection_json=selected,
            )
        )
        database.add(
            AudioTrackSelectionRow(file_path="/private/movie.mkv", selection_json=selected)
        )
        database.commit()
        from scanner.persistence.repository.community_imports import import_verified_download
        from scanner.persistence.repository.repository_community import set_connection
        from tests.test_community_verified_import import (
            BOUNDS,
            SERVICE,
            local_target,
            verified_input,
        )

        set_connection(
            database,
            credential_ref=only_identity_ref(database),
            service_url=SERVICE,
            contributor_id="reader",
            installation_id=source_identity.installation_id,
            key_id=source_identity.key_id,
        )
        database.commit()
        downloaded = verified_input()
        target_import = local_target(downloaded, source)
        from dataclasses import replace

        target_import = replace(target_import, media_item_id="media-fixture")
        import_verified_download(
            database,
            downloaded,
            target_import,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(database),
        )
        database.add(
            CommunityOperationRow(
                operation_id="pending-publication",
                service_url=SERVICE,
                kind="publish",
                state="submitted",
                contributor_id="reader",
                installation_id=source_identity.installation_id,
                contribution_id="root-publication",
                revision_id="original-submission",
                digest="a" * 64,
                scan_id="audio-fixture",
                consent_version="test-consent",
                updated_at="2026-10-03",
            )
        )
        database.add(
            CommunityOperationRow(
                operation_id="vote-backup-neutral",
                service_url=SERVICE,
                kind="vote",
                state="submitted",
                contributor_id="reader",
                installation_id=source_identity.installation_id,
                contribution_id="root-vote",
                revision_id="revision-vote",
                digest="b" * 64,
                scan_id="audio-fixture",
                vote="neutral",
                receipt='{"version":1,"vote":"neutral"}',
                updated_at="2026-10-04",
            )
        )
        database.flush()
        database.add(
            CommunityPublicationRow(
                operation_id="pending-publication",
                artifact_bytes=b"original-gzip",
                manifest_bytes=b"original-manifest",
                signature_bytes=b"original-signature",
                manifest_digest="b" * 64,
                signature_digest="c" * 64,
                preview_digest="d" * 64,
                consent_token="e" * 64,
                author_key_id=source_identity.key_id,
                source_kind="scan",
                source_id="audio-fixture",
                cancel_requested=True,
                error_code="network_unavailable",
            )
        )
        database.commit()
    engine.dispose()
    archive = tmp_path / "portable.backup"
    export_backup(
        source_db, source, archive, passphrase="test-only-password" if encrypted else None
    )
    return archive, source_identity, target


def _active_hashes(target):
    return {
        str(path.relative_to(target)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in target.rglob("*")
        if path.is_file()
    }


def test_full_restore_stages_pending_operation_without_new_remote_ids(tmp_path):
    _, source_identity, target = _fixture(tmp_path)
    source = tmp_path / "source"
    with sqlite3.connect(source / "scanner.db") as database:
        database.execute(
            "INSERT INTO community_connections "
            "(credential_ref, service_url, contributor_id, installation_id, key_id) "
            "VALUES ((SELECT min(credential_ref) FROM community_identities), "
            "?, ?, ?, ?)",
            (
                "https://pending.example",
                "contributor-1",
                source_identity.installation_id,
                source_identity.key_id,
            ),
        )
        database.execute(
            "INSERT INTO community_operations (operation_id, service_url, kind, state, "
            "contributor_id, installation_id, contribution_id, revision_id, digest, "
            "receipt, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "retry-1",
                "https://pending.example",
                "receipt",
                "submitted",
                "contributor-1",
                source_identity.installation_id,
                "contribution-1",
                "revision-1",
                "sha256:123",
                "signed-receipt",
                "2026-09-27",
            ),
        )
    archive = tmp_path / "pending.backup"
    export_backup(source / "scanner.db", source, archive)
    with preview_restore(archive, target) as preview:
        with sqlite3.connect(preview.stage / "database.sqlite3") as database:
            assert database.execute(
                "SELECT operation_id, state, contribution_id, receipt FROM community_operations "
                "WHERE operation_id='retry-1'"
            ).fetchall() == [("retry-1", "submitted", "contribution-1", "signed-receipt")]


@pytest.mark.parametrize("encrypted", [False, True])
def test_preview_stages_all_work_without_leaking_secrets_or_touching_target(tmp_path, encrypted):
    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional age transport not installed")
    archive, source_identity, target = _fixture(tmp_path, encrypted=encrypted)
    before = _active_hashes(target)
    kwargs = {"passphrase": "test-only-password"} if encrypted else {}
    with preview_restore(archive, target, **kwargs) as preview:
        plan = preview.plan
        assert set(plan) == {
            "format",
            "kind",
            "protection",
            "source_schema",
            "staged_schema",
            "source_identity",
            "account_identities",
            "omitted_environment",
            "included_secret_types",
            "administrator_included",
            "backup_deployment",
        }
        assert plan["kind"] == "full"
        assert plan["protection"] == ("age-scrypt" if encrypted else "plain")
        assert plan["source_identity"]["key_id"] == source_identity.key_id
        assert "private-canary" not in json.dumps(plan)
        assert "test-only-password" not in json.dumps(plan)
        with sqlite3.connect(preview.stage / "database.sqlite3") as database:
            assert database.execute("SELECT api_key FROM jellyfin_settings").fetchone() == (
                "private-canary",
            )
            rows = database.execute("SELECT confirmed_identity_json FROM media_items").fetchall()
            assert json.loads(rows[0][0])["identifiers"] == [
                {
                    "provider": "tmdb",
                    "entity_type": "movie",
                    "value": "501",
                    "provenance": "user_confirmed",
                }
            ]
            memory = database.execute(
                "SELECT selection_json FROM audio_track_selections"
            ).fetchall()
            provenance = database.execute(
                "SELECT requested_audio_selection_json, analyzed_audio_selection_json FROM scans"
            ).fetchall()
            assert json.loads(memory[0][0])["track"]["title"] == "Main"
            assert provenance[0][0] == provenance[0][1] == memory[0][0]
            imported = database.execute(
                "SELECT artifact_bytes, manifest_bytes, signature_bytes, historical_key_json, "
                "provenance_json, event_ids_json, receipt_json "
                "FROM community_imports"
            ).fetchall()
            targets = database.execute(
                "SELECT import_id, media_item_id, media_source_id, target_evidence_json "
                "FROM community_import_targets"
            ).fetchall()
            receipts = database.execute(
                "SELECT operation_id, receipt, scan_id FROM community_operations "
                "WHERE kind='receipt'"
            ).fetchall()
            votes = database.execute(
                "SELECT operation_id, state, revision_id, vote, receipt, contributor_id "
                "FROM community_operations WHERE kind='vote'"
            ).fetchall()
            assert votes == (
                [
                    (
                        "vote-backup-neutral",
                        "submitted",
                        "revision-vote",
                        "neutral",
                        '{"version":1,"vote":"neutral"}',
                        "reader",
                    )
                ]
            )
            from tests.test_community_verified_import import download_input

            assert imported[0][:3] == download_input()[2]
            assert all(imported[0][3:])
            assert len(targets) == len(receipts) == 1
            assert targets[0][1:3] == ("media-fixture", "")
            assert receipts[0][1] == imported[0][-1]
            publications = database.execute(
                "SELECT p.operation_id, p.artifact_bytes, p.manifest_bytes, p.signature_bytes, "
                "p.cancel_requested, p.error_code, o.state, o.revision_id "
                "FROM community_publications p JOIN community_operations o USING (operation_id)"
            ).fetchall()
            assert publications == (
                [
                    (
                        "pending-publication",
                        b"original-gzip",
                        b"original-manifest",
                        b"original-signature",
                        1,
                        "network_unavailable",
                        "submitted",
                        "original-submission",
                    )
                ]
            )
        staging = preview.stage
    assert not staging.exists()
    assert _active_hashes(target) == before


@pytest.mark.parametrize("damage", ["wrong-password", "missing-password", "truncated", "corrupt"])
def test_failed_encrypted_preflight_leaves_target_unchanged(tmp_path, damage):
    if not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional age transport not installed")
    archive, _, target = _fixture(tmp_path, encrypted=True)
    before = _active_hashes(target)
    if damage == "truncated":
        with archive.open("r+b") as stream:
            stream.truncate(archive.stat().st_size - 12)
    elif damage == "corrupt":
        with archive.open("r+b") as stream:
            stream.seek(-1, 2)
            byte = stream.read(1)
            stream.seek(-1, 2)
            stream.write(bytes([byte[0] ^ 1]))
    passphrase = (
        None
        if damage == "missing-password"
        else ("wrong" if damage == "wrong-password" else "test-only-password")
    )
    with pytest.raises((BackupError, AgeUnavailable)):
        with preview_restore(archive, target, passphrase=passphrase):
            pass
    assert _active_hashes(target) == before


@pytest.mark.parametrize("damage", ["traversal", "symlink", "oversized", "future", "checksum"])
def test_bad_plain_archive_never_mutates_target(tmp_path, damage):
    archive, _, target = _fixture(tmp_path)
    before = _active_hashes(target)
    with tarfile.open(archive, "r:") as original:
        manifest = json.load(original.extractfile("manifest.json"))
        database = original.extractfile("database.sqlite3").read()
    if damage == "future":
        manifest["format"] = "editarr-backup-2"
    elif damage == "checksum":
        manifest["files"]["database.sqlite3"]["sha256"] = "0" * 64
    elif damage == "oversized":
        manifest["files"]["database.sqlite3"]["size"] = 64 * 1024**3 + 1
    altered = tmp_path / "bad.tar"
    with tarfile.open(altered, "w:") as output:
        raw = json.dumps(manifest).encode()
        member = tarfile.TarInfo("manifest.json")
        member.size = len(raw)
        output.addfile(member, io.BytesIO(raw))
        member = tarfile.TarInfo("database.sqlite3")
        member.size = len(database)
        output.addfile(member, io.BytesIO(database))
        if damage in {"traversal", "symlink"}:
            member = tarfile.TarInfo("../outside" if damage == "traversal" else "deployment.json")
            member.type = tarfile.SYMTYPE if damage == "symlink" else tarfile.REGTYPE
            member.linkname = "../outside" if damage == "symlink" else ""
            output.addfile(member, io.BytesIO(b""))
    with pytest.raises((BackupError, tarfile.TarError)):
        with preview_restore(altered, target):
            pass
    assert _active_hashes(target) == before
    assert not (tmp_path / "outside").exists()


def test_plain_preflight_does_not_call_age_or_request_password(tmp_path, monkeypatch):
    from scanner.backup import restore

    archive, _, target = _fixture(tmp_path)
    monkeypatch.setattr(restore, "run_age", lambda *_args, **_kwargs: pytest.fail("age called"))
    with preview_restore(archive, target) as preview:
        assert preview.plan["protection"] == "plain"
    with pytest.raises(BackupError, match="does not accept"):
        with preview_restore(archive, target, passphrase="unneeded"):
            pass


@pytest.mark.parametrize("revision", [None, "0024_account_dispositions"])
def test_a_backup_below_schema_head_is_refused_without_changes(tmp_path, revision):
    archive, _, target = _fixture(tmp_path)
    legacy = tmp_path / "legacy.db"
    if revision is None:
        create_unversioned_fixture(legacy).dispose()
    else:
        _upgrade(legacy, revision)
    before = _active_hashes(target)
    old_bytes = legacy.read_bytes()
    rewritten = tmp_path / "old-backup.tar"
    with tarfile.open(archive, "r:") as source:
        manifest = json.load(source.extractfile("manifest.json"))
        manifest["schema_revision"] = revision or "unversioned-v1"
        manifest["files"]["database.sqlite3"] = {
            "size": len(old_bytes),
            "sha256": hashlib.sha256(old_bytes).hexdigest(),
        }
        with tarfile.open(rewritten, "w:") as output:
            payload = json.dumps(manifest).encode()
            members = {"manifest.json": payload, "database.sqlite3": old_bytes}
            for member in source:
                if member.name not in members:
                    members[member.name] = source.extractfile(member).read()
            for name, payload in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                output.addfile(info, io.BytesIO(payload))
    with pytest.raises(RestoreRefused) as refused:
        with preview_restore(rewritten, target):
            pass
    assert refused.value.reason == "restore_backup_schema_unsupported"
    assert str(refused.value) == (
        f"This backup has database schema {revision or 'unversioned-v1'}. This Editarr "
        "restores only backups made at schema 0033_scan_worker_timing."
    )
    assert legacy.read_bytes() == old_bytes
    assert _active_hashes(target) == before


def test_untrusted_manifest_cannot_echo_secrets_into_preview(tmp_path):
    archive, _, target = _fixture(tmp_path)
    before = _active_hashes(target)
    with tarfile.open(archive, "r:") as source:
        manifest = json.load(source.extractfile("manifest.json"))
        manifest["omitted_environment"] = ["private-canary"]
        contents = {
            member.name: source.extractfile(member).read()
            for member in source
            if member.name != "manifest.json"
        }
    altered = tmp_path / "untrusted.tar"
    with tarfile.open(altered, "w:") as output:
        entries = {"manifest.json": json.dumps(manifest).encode(), **contents}
        for name, payload in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            output.addfile(info, io.BytesIO(payload))
    with pytest.raises(BackupError, match="manifest metadata"):
        with preview_restore(altered, target):
            pass
    assert _active_hashes(target) == before


def test_future_age_work_factor_rejected_before_decryption(tmp_path):
    archive, _, target = _fixture(tmp_path)
    before = _active_hashes(target)
    bogus = tmp_path / "future.age"
    bogus.write_bytes(b"age-encryption.org/v1\n-> scrypt AAAAAAAAAAAAAAAAAAAAAA 19\n")
    with pytest.raises(AgeUnavailable, match="work factor"):
        with preview_restore(bogus, target, passphrase="test-only-password"):
            pass
    assert _active_hashes(target) == before


def test_private_staging_is_removed_on_exit_and_failed_preflight(tmp_path):
    archive, _, target = _fixture(tmp_path)
    parent = tmp_path / "staging"
    parent.mkdir(mode=0o700)
    with preview_restore(archive, target, staging_parent=parent) as preview:
        assert preview.stage.exists()
        assert not list(parent.glob(".editarr-restore-*/decrypted.tar"))
    assert not list(parent.glob(".editarr-restore-*"))
    with archive.open("ab") as output:
        output.write(b"trailing")
    with pytest.raises(BackupError):
        with preview_restore(archive, target, staging_parent=parent):
            pass
    assert not list(parent.glob(".editarr-restore-*"))


def test_interrupted_preflight_recovers_private_plaintext_on_next_attempt(tmp_path):
    archive, _, target = _fixture(tmp_path)
    parent = tmp_path / "staging"
    parent.mkdir(mode=0o700)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; from pathlib import Path; "
            "from scanner.backup.restore import preview_restore; "
            "with_expr = preview_restore(Path(sys.argv[1]), Path(sys.argv[2]), "
            "staging_parent=Path(sys.argv[3])); "
            "with_expr.__enter__(); os._exit(47)",
            str(archive),
            str(target),
            str(parent),
        ],
        check=False,
    )
    assert result.returncode == 47
    orphan = next(parent.glob(".editarr-restore-*"))
    assert stat.S_IMODE(orphan.stat().st_mode) == 0o700
    assert (orphan / "content/database.sqlite3").is_file()
    with preview_restore(archive, target, staging_parent=parent):
        assert not orphan.exists()
    assert not list(parent.glob(".editarr-restore-*"))
