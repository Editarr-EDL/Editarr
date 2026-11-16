"""Portable rotation generations remain coherent without changing hosted authority."""

import asyncio
import shutil
import sqlite3

import httpx
import pytest

from scanner.backup.archive import export_backup
from scanner.backup.restore import preview_restore
from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from scanner.security.community_key_rotation import activate_rotation, prepare_rotation
from tests.community_identity_support import identity_home
from tests.test_community_rotation import outcome


@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("generation", ["pending", "completed", "second_pending"])
def test_generation_roundtrip(tmp_path, encrypted, generation):
    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional age and Expect transport not installed")
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    database = source / "scanner.db"
    migrate_fresh_database(database, source)
    load_or_create_installation_identity(identity_home(source))
    save_identity_registration(
        identity_home(source),
        contributor_id="contributor-001",
        service_url="https://community.test",
        last_known_key_state="active",
    )
    identity = prepare_rotation(identity_home(source), "community.test")
    if generation != "pending":
        identity = activate_rotation(
            identity_home(source), identity.rotation["operationId"], outcome(identity)
        )
    if generation == "second_pending":
        identity = prepare_rotation(identity_home(source), "community.test")
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO community_connections "
            "(credential_ref, service_url, contributor_id, installation_id, key_id) "
            "VALUES ((SELECT min(credential_ref) FROM community_identities), "
            "?, ?, ?, ?)",
            (
                identity.service_url,
                identity.contributor_id,
                identity.installation_id,
                identity.key_id,
            ),
        )
    archive = tmp_path / "identity.backup"
    password = "synthetic-test-only" if encrypted else None
    manifest = export_backup(database, source, archive, passphrase=password)
    assert manifest["identity_key_id"] == identity.key_id
    assert "rotation.lock" not in str(manifest)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    with preview_restore(archive, target, passphrase=password) as preview:
        restored = load_or_create_installation_identity(identity_home(preview.stage))
        assert restored.key_id == identity.key_id
        assert restored.key_file == identity.key_file
        assert restored.rotation == identity.rotation
        assert restored.private_key.private_bytes_raw() == identity.private_key.private_bytes_raw()
        if identity.pending_replacement:
            assert (
                restored.pending_replacement.private_key.private_bytes_raw()
                == identity.pending_replacement.private_key.private_bytes_raw()
            )
        with sqlite3.connect(preview.stage / "database.sqlite3") as db:
            assert db.execute("SELECT key_id FROM community_connections").fetchone() == (
                identity.key_id,
            )


def test_restored_retired_generation_never_creates_or_reactivates_key(tmp_path):
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    database = source / "scanner.db"
    migrate_fresh_database(database, source)
    load_or_create_installation_identity(identity_home(source))
    old = save_identity_registration(
        identity_home(source),
        contributor_id="contributor-001",
        service_url="https://community.test",
        last_known_key_state="active",
    )
    archive = tmp_path / "old.backup"
    export_backup(database, source, archive)
    pending = prepare_rotation(identity_home(source), "community.test")
    active = activate_rotation(
        identity_home(source), pending.rotation["operationId"], outcome(pending)
    )
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    with preview_restore(archive, target) as preview:
        restored = load_or_create_installation_identity(identity_home(preview.stage))
        assert restored.key_id == old.key_id != active.key_id

        def denied(request):
            assert old.key_id in request.headers["signature-input"]
            return httpx.Response(401, json={"error": {"code": "key_inactive"}})

        transport = CommunityTransport(
            ServiceEndpoint("https://community.test", "community.test"),
            adapter=httpx.MockTransport(denied),
        )
        with pytest.raises(CommunityFailure, match="key_inactive"):
            asyncio.run(transport.request("GET", "/v1/identity", identity=restored))
        assert (
            load_or_create_installation_identity(identity_home(preview.stage)).pending_replacement
            is None
        )
        assert (
            load_or_create_installation_identity(identity_home(preview.stage)).key_id == old.key_id
        )


@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("generation", ["pending", "completed"])
def test_recovery_generation_roundtrip_without_old_private_key(tmp_path, encrypted, generation):
    from scanner.security.community_key_recovery import (
        activate_recovery,
        bind_recovery,
        prepare_recovery,
        recovery_source,
    )
    from scanner.security.private_identity_files import IdentityUnavailable
    from tests.test_community_recovery import outcome as recovery_outcome

    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional age and Expect transport not installed")
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    database = source / "scanner.db"
    migrate_fresh_database(database, source)
    load_or_create_installation_identity(identity_home(source))
    old = save_identity_registration(
        identity_home(source),
        contributor_id="contributor-001",
        service_url="https://community.test",
        last_known_key_state="active",
    )
    (identity_home(source).root / old.key_file).unlink()
    prepare_recovery(identity_home(source), "community.test", old.key_id)
    identity = bind_recovery(identity_home(source), "b" * 64)
    if generation == "completed":
        identity = activate_recovery(identity_home(source), recovery_outcome(source)["result"])
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO community_connections "
            "(credential_ref, service_url, contributor_id, installation_id, key_id) "
            "VALUES ((SELECT min(credential_ref) FROM community_identities), "
            "?, ?, ?, ?)",
            (
                identity.service_url,
                identity.contributor_id,
                identity.installation_id,
                identity.key_id,
            ),
        )
    archive = tmp_path / "recovery.backup"
    password = "synthetic-test-only" if encrypted else None
    manifest = export_backup(database, source, archive, passphrase=password)
    assert "community-recovery-proof" in manifest["included_secret_types"]
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    with preview_restore(archive, target, passphrase=password) as preview:
        restored = recovery_source(identity_home(preview.stage))
        assert restored.recovery == identity.recovery
        assert restored.installation_id == old.installation_id
        if generation == "pending":
            assert restored.private_key is None
            with pytest.raises(IdentityUnavailable):
                load_or_create_installation_identity(identity_home(preview.stage))
        else:
            assert (
                restored.private_key.private_bytes_raw() == identity.private_key.private_bytes_raw()
            )
