"""Restore preflight refuses damaged, foreign, or unsupported archives without side effects."""

import hashlib
import io
import json
import shutil
import stat
import subprocess
import sys
import tarfile

import pytest

from scanner.backup.age_transport import AgeUnavailable
from scanner.backup.archive import BackupError
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused
from tests.activity_migration_support import _upgrade
from tests.community_schema_fixture import create_unversioned_fixture
from tests.test_backup_restore import _active_hashes, _fixture


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
        "restores only backups made at schema 0040_review_community_filters."
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
