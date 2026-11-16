import base64
import hashlib
import json
import tarfile
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.backup.archive import export_backup
from scanner.backup.restore import preview_restore
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security.community_identity import load_or_create_installation_identity
from scanner.security.community_signatures import sign_request
from tests.community_identity_support import client_home, identity_home

FIXTURES = Path(__file__).parent / "fixtures/community"


def test_python_reproduces_independent_worker_completion_vector():
    vector = json.loads((FIXTURES / "local-enrollment-request-v1.json").read_bytes())
    contract = json.loads((FIXTURES / "enrollment-profile-v1.json").read_bytes())
    body = json.loads(vector["body"])
    assert list(body) == contract["completionRequiredFields"]
    assert vector["url"].endswith(contract["completionPath"])
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert base64.b64encode(public).decode() == vector["publicKey"]
    signed = sign_request(
        private_key=key,
        key_id="sha256:" + hashlib.sha256(public).hexdigest(),
        method="POST",
        url=vector["url"],
        body=vector["body"].encode(),
        created=1700000030,
        expires=1700000090,
        nonce="AQIDBAUGBwgJCgsMDQ4PEA",
        content_type="application/json",
        operation_id=body["stateId"],
    )
    assert {name: value for name, value in signed.items() if isinstance(value, str)} == vector[
        "headers"
    ]


def test_pending_enrollment_survives_archive_and_staged_restore(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o700)
    database = data_dir / "scanner.db"
    migrate_fresh_database(database, data_dir)
    engine = create_engine(f"sqlite:///{database}")
    with Session(engine) as db:
        transport = CommunityTransport(
            ServiceEndpoint("https://community.example", "community.example"),
            adapter=httpx.MockTransport(lambda _: pytest.fail("no request")),
        )
        client = CommunityClient(db, client_home(db, data_dir), transport)
        client.start()
        identity = load_or_create_installation_identity(identity_home(data_dir))
        secret = identity.client_state["pending"]["secret"]
    engine.dispose()
    archive_path = tmp_path / "export.tar"
    inventory = export_backup(database, data_dir, archive_path)
    assert "community-enrollment-confirmation" in inventory["included_secret_types"]
    with tarfile.open(archive_path, "r:") as archive:
        manifest = json.loads(archive.extractfile("secrets/community/identity.json").read())
        assert manifest["client_state"]["pending"]["secret"] == secret
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    with preview_restore(archive_path, target) as preview:
        assert preview.plan["source_identity"] == {
            "installation_id": identity.installation_id,
            "key_id": identity.key_id,
        }
        assert secret not in repr(preview.plan)
