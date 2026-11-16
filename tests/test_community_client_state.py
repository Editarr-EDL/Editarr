import json

import pytest

from scanner.security.community_client_state import update_client_state
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    read_identity_backup_snapshot,
)
from scanner.security.private_identity_files import IdentityUnavailable
from tests.community_identity_support import identity_home


def test_journal_survives_restart_and_backup_without_public_secret(tmp_path):
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    pending = {
        "service_url": "https://community.test",
        "audience": "community.test",
        "secret": "a" * 64,
        "state_id": None,
        "expires_at": 1234,
    }
    state = {"version": 1, "pending": pending, "profile": None}
    updated = update_client_state(identity_home(tmp_path), expected=None, state=state)
    assert updated.key_id == identity.key_id
    assert "a" * 64 not in repr(updated)
    assert load_or_create_installation_identity(identity_home(tmp_path)).client_state == state
    snapshot = read_identity_backup_snapshot(tmp_path, identity_home(tmp_path).root)
    manifest = json.loads(dict(snapshot.files)["secrets/community/identity.json"])
    assert manifest["client_state"] == state
    with pytest.raises(IdentityUnavailable, match="changed"):
        update_client_state(identity_home(tmp_path), expected=None, state=state)


def test_corrupt_journal_never_creates_new_key(tmp_path):
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    path = identity_home(tmp_path).root / "identity.json"
    manifest = json.loads(path.read_bytes())
    manifest["client_state"] = {"version": 999}
    path.write_text(json.dumps(manifest))
    with pytest.raises(IdentityUnavailable, match="client state"):
        load_or_create_installation_identity(identity_home(tmp_path))
    assert json.loads(path.read_bytes())["key_id"] == identity.key_id
