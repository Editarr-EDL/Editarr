"""Portable community identity storage and recovery checks."""

import hashlib
import json
import multiprocessing
import os
import shutil
import stat
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from scanner.security import community_identity
from scanner.security.community_identity import (
    IdentityUnavailable,
    load_or_create_installation_identity,
    read_identity_backup_snapshot,
    save_identity_registration,
    stage_identity_replacement,
)
from tests.community_identity_support import identity_home


def _identity_ids(data_dir: Path) -> tuple[str, str]:
    identity = load_or_create_installation_identity(identity_home(data_dir))
    return identity.installation_id, identity.key_id


def test_concurrent_creation_restart_and_durable_volume_move(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as executor:
        identities = list(
            executor.map(
                lambda _: load_or_create_installation_identity(identity_home(tmp_path)), range(4)
            )
        )
    assert len({identity.key_id for identity in identities}) == 1
    assert len({identity.installation_id for identity in identities}) == 1
    current = load_or_create_installation_identity(identity_home(tmp_path))
    assert current.key_id == identities[0].key_id
    moved = tmp_path.parent / "moved-data"
    shutil.copytree(tmp_path, moved)
    restored = load_or_create_installation_identity(identity_home(moved))
    assert restored.key_id == current.key_id
    assert restored.installation_id == current.installation_id
    challenge = b"local signing proof"
    restored.private_key.public_key().verify(current.private_key.sign(challenge), challenge)


def test_separate_processes_publish_one_identity(tmp_path):
    with ProcessPoolExecutor(
        max_workers=3, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        identities = list(pool.map(_identity_ids, [tmp_path] * 6))
    assert len(set(identities)) == 1
    assert _identity_ids(tmp_path) == identities[0]


def test_missing_external_key_or_manifest_never_generates_a_replacement(tmp_path):
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    root = identity_home(tmp_path).root
    original_manifest = (root / community_identity.MANIFEST_FILE).read_bytes()
    (root / community_identity.KEY_FILE).unlink()
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "incomplete"
    assert (root / community_identity.MANIFEST_FILE).read_bytes() == original_manifest
    assert identity.key_id.encode() in original_manifest
    (root / community_identity.MANIFEST_FILE).unlink()
    (root / community_identity.KEY_FILE).write_bytes(b"corrupt")
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "incomplete"


def test_corrupt_key_bad_permissions_and_symlink_are_rejected(tmp_path):
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    root = identity_home(tmp_path).root
    key_path = root / community_identity.KEY_FILE
    key_path.write_bytes(b"sensitive-not-a-key\n")
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "corrupt"
    assert "sensitive-not-a-key" not in str(error.value)
    key_path.unlink()
    (root / community_identity.MANIFEST_FILE).unlink()
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    key_path.chmod(0o640)
    if os.name == "posix":
        with pytest.raises(IdentityUnavailable, match="not private"):
            load_or_create_installation_identity(identity_home(tmp_path))
    key_path.chmod(0o600)
    assert load_or_create_installation_identity(identity_home(tmp_path)).key_id == identity.key_id
    key_path.unlink()
    target = tmp_path / "outside"
    target.write_bytes(b"should-not-be-read")
    key_path.symlink_to(target)
    with pytest.raises(IdentityUnavailable):
        load_or_create_installation_identity(identity_home(tmp_path))
    assert target.read_bytes() == b"should-not-be-read"


def test_interrupted_key_publication_leaves_recoverable_manifest(monkeypatch, tmp_path):
    original_write = community_identity._atomic_write

    def fail_key(path, contents):
        if path.name == community_identity.KEY_FILE:
            raise IdentityUnavailable("unavailable", "Simulated write failure")
        original_write(path, contents)

    monkeypatch.setattr(community_identity, "_atomic_write", fail_key)
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "unavailable"
    monkeypatch.setattr(community_identity, "_atomic_write", original_write)
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "incomplete"
    root = identity_home(tmp_path).root
    assert (root / community_identity.MANIFEST_FILE).exists()
    assert not (root / community_identity.KEY_FILE).exists()


def test_failed_atomic_publish_does_not_leave_secret_temporary_files(monkeypatch, tmp_path):
    original_replace = community_identity.os.replace

    def deny_replace(source, destination):
        if Path(destination).name == community_identity.KEY_FILE:
            raise PermissionError("Simulated permission failure")
        original_replace(source, destination)

    monkeypatch.setattr(community_identity.os, "replace", deny_replace)
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "unavailable"
    root = identity_home(tmp_path).root
    assert not list(root.glob(".current.key-*"))
    monkeypatch.setattr(community_identity.os, "replace", original_replace)
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "incomplete"


def test_manifest_corruption_and_unsafe_directory_refuse_silent_repair(tmp_path):
    load_or_create_installation_identity(identity_home(tmp_path))
    root = identity_home(tmp_path).root
    manifest_path = root / community_identity.MANIFEST_FILE
    original = manifest_path.read_bytes()
    manifest_path.write_bytes(original.replace(b'"key_id":', b'"key_id":"other","key_id":', 1))
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "corrupt"
    manifest = json.loads(original)
    manifest["schema_version"] = "community-identity-2"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "corrupt"
    manifest_path.write_bytes(original)
    relocated = tmp_path / "real-secrets"
    (tmp_path / "secrets").rename(relocated)
    (tmp_path / "secrets").symlink_to(relocated)
    with pytest.raises(IdentityUnavailable, match="unsafe"):
        load_or_create_installation_identity(identity_home(tmp_path))


def test_public_fingerprint_and_private_permissions(tmp_path):
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    assert identity.key_id == "sha256:" + hashlib.sha256(identity.public_key).hexdigest()
    assert "private_key" not in repr(identity)
    root = identity_home(tmp_path).root
    if os.name == "posix":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        for filename in (community_identity.KEY_FILE, community_identity.MANIFEST_FILE):
            assert stat.S_IMODE((root / filename).stat().st_mode) == 0o600


def test_registered_identity_round_trips_without_key_rotation(tmp_path):
    original = load_or_create_installation_identity(identity_home(tmp_path))
    enrolled = save_identity_registration(
        identity_home(tmp_path),
        contributor_id="contributor-1",
        service_url="https://community.example",
        last_known_key_state="active",
    )
    assert enrolled.key_id == original.key_id
    assert enrolled.installation_id == original.installation_id
    assert enrolled.contributor_id == "contributor-1"
    assert (
        load_or_create_installation_identity(identity_home(tmp_path)).service_url
        == "https://community.example"
    )
    retired = save_identity_registration(
        identity_home(tmp_path),
        contributor_id="contributor-1",
        service_url="https://community.example",
        last_known_key_state="retired",
    )
    assert retired.last_known_key_state == "retired"
    assert retired.key_id == original.key_id
    with pytest.raises(IdentityUnavailable):
        save_identity_registration(
            identity_home(tmp_path),
            contributor_id="different",
            service_url="https://community.example",
            last_known_key_state="active",
        )


@pytest.mark.parametrize(
    "service_url",
    [
        "http://community.example",
        "https://community.example\n",
        "https://COMMUNITY.example",
        "https://user@community.example",
        "https://community.example#",
        "https://community.example?",
        "https://community.example/%ZZ",
        "https://community.example\\other",
    ],
)
def test_invalid_service_uri_never_changes_identity(tmp_path, service_url):
    original = load_or_create_installation_identity(identity_home(tmp_path))
    with pytest.raises(IdentityUnavailable):
        save_identity_registration(
            identity_home(tmp_path),
            contributor_id="contributor-1",
            service_url=service_url,
            last_known_key_state="active",
        )
    current = load_or_create_installation_identity(identity_home(tmp_path))
    assert current.key_id == original.key_id
    assert current.contributor_id is None


def test_pending_replacement_is_persistent_idempotent_and_never_activates(tmp_path):
    with pytest.raises(IdentityUnavailable):
        stage_identity_replacement(identity_home(tmp_path), operation_id="rotate-1")
    original = load_or_create_installation_identity(identity_home(tmp_path))
    save_identity_registration(
        identity_home(tmp_path),
        contributor_id="contributor-1",
        service_url="https://community.example",
        last_known_key_state="active",
    )
    staged = stage_identity_replacement(identity_home(tmp_path), operation_id="rotate-1")
    assert staged.key_id == original.key_id
    assert staged.pending_replacement is not None
    assert staged.pending_replacement.key_id != original.key_id
    assert "private_key" not in repr(staged.pending_replacement)
    again = stage_identity_replacement(identity_home(tmp_path), operation_id="rotate-1")
    assert again.pending_replacement.key_id == staged.pending_replacement.key_id
    with pytest.raises(IdentityUnavailable) as error:
        stage_identity_replacement(identity_home(tmp_path), operation_id="rotate-2")
    assert error.value.status == "pending"
    restarted = load_or_create_installation_identity(identity_home(tmp_path))
    assert restarted.key_id == original.key_id
    assert restarted.pending_replacement.key_id == staged.pending_replacement.key_id
    assert restarted.pending_replacement.operation_id == "rotate-1"
    updated = save_identity_registration(
        identity_home(tmp_path),
        contributor_id="contributor-1",
        service_url="https://community.example",
        last_known_key_state="retired",
    )
    assert updated.pending_replacement.key_id == staged.pending_replacement.key_id
    assert updated.key_id == original.key_id
    restored_root = tmp_path.parent / "moved-pending"
    shutil.copytree(tmp_path, restored_root)
    assert load_or_create_installation_identity(
        identity_home(restored_root)
    ).pending_replacement.key_id == (staged.pending_replacement.key_id)


def test_missing_pending_file_requires_recovery_not_a_new_replacement(tmp_path):
    load_or_create_installation_identity(identity_home(tmp_path))
    save_identity_registration(
        identity_home(tmp_path),
        contributor_id="contributor-1",
        service_url="https://community.example",
        last_known_key_state="active",
    )
    staged = stage_identity_replacement(identity_home(tmp_path), operation_id="rotate-1")
    (identity_home(tmp_path).root / community_identity.PENDING_FILE).unlink()
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "incomplete"
    with pytest.raises(IdentityUnavailable):
        stage_identity_replacement(identity_home(tmp_path), operation_id="rotate-1")
    assert staged.pending_replacement is not None


def test_backup_snapshot_includes_current_and_pending_key_without_lock_or_secret_repr(tmp_path):
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    first = read_identity_backup_snapshot(tmp_path, identity_home(tmp_path).root)
    assert first.key_id == identity.key_id
    assert [name for name, _ in first.files] == [
        "secrets/community/identity.json",
        "secrets/community/current.key",
    ]
    assert "files=" not in repr(first)
    save_identity_registration(
        identity_home(tmp_path),
        contributor_id="contributor-1",
        service_url="https://community.example",
        last_known_key_state="active",
    )
    stage_identity_replacement(identity_home(tmp_path), operation_id="rotate-1")
    staged = read_identity_backup_snapshot(tmp_path, identity_home(tmp_path).root)
    assert [name for name, _ in staged.files] == [
        "secrets/community/identity.json",
        "secrets/community/current.key",
        "secrets/community/pending.key",
    ]
    root = identity_home(tmp_path).root
    for name, contents in staged.files:
        assert contents == (root / Path(name).name).read_bytes()
    assert (root / community_identity.PENDING_FILE).read_bytes() not in repr(staged).encode()


def test_interrupted_pending_slot_never_rotates_or_generates_another_key(monkeypatch, tmp_path):
    original = load_or_create_installation_identity(identity_home(tmp_path))
    save_identity_registration(
        identity_home(tmp_path),
        contributor_id="contributor-1",
        service_url="https://community.example",
        last_known_key_state="active",
    )
    original_write = community_identity._atomic_write

    def fail_manifest(path, contents):
        if path.name == community_identity.MANIFEST_FILE:
            raise IdentityUnavailable("unavailable", "Simulated interrupted staging")
        original_write(path, contents)

    monkeypatch.setattr(community_identity, "_atomic_write", fail_manifest)
    with pytest.raises(IdentityUnavailable):
        stage_identity_replacement(identity_home(tmp_path), operation_id="rotate-1")
    monkeypatch.setattr(community_identity, "_atomic_write", original_write)
    with pytest.raises(IdentityUnavailable) as error:
        load_or_create_installation_identity(identity_home(tmp_path))
    assert error.value.status == "incomplete"
    assert (identity_home(tmp_path).root / community_identity.KEY_FILE).exists()
    assert (
        original.key_id
        in (identity_home(tmp_path).root / community_identity.MANIFEST_FILE).read_text()
    )
