"""Explicit rotation failures use synthetic keys, temporary SQLite and mock HTTPS."""

import asyncio
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import Base
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.rotation import CommunityRotation
from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.security import community_identity as store
from scanner.security.community_key_rotation import prepare_rotation
from scanner.security.private_identity_files import IdentityUnavailable
from tests.community_identity_support import client_home, identity_home, only_connection
from tests.test_community_client import profile


@pytest.fixture
def context(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        identity = store.load_or_create_installation_identity(identity_home(tmp_path))
        client = make_client(db, tmp_path, lambda _: httpx.Response(200, json=profile(identity)))
        client.start()
        asyncio.run(client.complete("b" * 64))
        yield db, tmp_path
    engine.dispose()


def make_client(db, root, handler):
    async def delay(_):
        pass

    return CommunityClient(
        db,
        client_home(db, root),
        CommunityTransport(
            ServiceEndpoint("https://community.test", "community.test"),
            adapter=httpx.MockTransport(handler),
            delay=delay,
        ),
    )


def outcome(identity):
    record = identity.rotation
    return {
        "version": 1,
        "kind": "key_rotation",
        "operationId": record["operationId"],
        "contributorId": identity.contributor_id,
        "installationId": identity.installation_id,
        "oldKeyId": record["oldKeyId"],
        "newKeyId": record["newKeyId"],
        "committedAt": 1700000030000,
    }


def committed(root):
    return {
        "version": 1,
        "state": "completed",
        "result": outcome(store.load_or_create_installation_identity(identity_home(root))),
    }


def test_response_loss_uses_same_candidate_and_new_signed_probe_after_restart(context):
    db, root = context
    requests = []
    old = store.load_or_create_installation_identity(identity_home(root))

    def lost(request):
        requests.append(request)
        raise httpx.ReadError("lost after commit", request=request)

    with pytest.raises(CommunityFailure, match="network_unavailable"):
        asyncio.run(CommunityRotation(make_client(db, root, lost)).run(prepare=True))
    pending = store.load_or_create_installation_identity(identity_home(root))
    assert pending.key_id == old.key_id
    assert pending.pending_replacement is not None
    assert pending.rotation["candidatePrivateKey"] not in json.dumps(
        make_client(db, root, lost).status()
    )

    def probe(request):
        requests.append(request)
        assert request.url.path == "/v1/keys/rotation-status"
        assert pending.pending_replacement.key_id in request.headers["signature-input"]
        return httpx.Response(200, json=committed(root))

    result = asyncio.run(CommunityRotation(make_client(db, root, probe)).run(resend=True))
    assert len({request.content for request in requests}) == 1
    assert len({request.headers["idempotency-key"] for request in requests}) == 1
    assert result["pendingRotation"] is None and result["rotationCompleted"]
    assert result["identity"]["keyId"] == pending.pending_replacement.key_id
    assert (
        only_connection(db, "https://community.test").key_id == pending.pending_replacement.key_id
    )


def test_before_send_cancellation_and_not_committed_retry_do_not_generate_again(context):
    db, root = context
    requests = []

    def cancelled(request):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(CommunityRotation(make_client(db, root, cancelled)).run(prepare=True))
    original = store.load_or_create_installation_identity(identity_home(root))

    def retry(request):
        requests.append(request)
        if request.url.path.endswith("rotation-status"):
            return httpx.Response(
                200, json={"version": 1, "state": "not_committed", "result": None}
            )
        return httpx.Response(200, json=committed(root))

    rotation = CommunityRotation(make_client(db, root, retry))
    assert asyncio.run(rotation.run())["rotationStatus"] == "not_committed"
    assert (
        store.load_or_create_installation_identity(identity_home(root)).rotation
        == original.rotation
    )
    asyncio.run(rotation.run(resend=True))
    assert [request.url.path for request in requests] == [
        "/v1/keys/rotation-status",
        "/v1/keys/rotation-status",
        "/v1/keys/rotate",
    ]
    assert all(request.content == original.rotation["body"].encode() for request in requests)


@pytest.mark.parametrize("point", ["key_write", "manifest_write", "projection"])
def test_activation_crash_retains_complete_generation_and_recovers(context, monkeypatch, point):
    db, root = context
    client = make_client(db, root, lambda _: httpx.Response(200, json=committed(root)))
    old = store.load_or_create_installation_identity(identity_home(root))
    original_write = store._atomic_write
    original_project = client._project

    def failing_write(path, raw):
        record = json.loads(raw) if path.name == store.MANIFEST_FILE else None
        if (point == "key_write" and path.name == store.PENDING_FILE) or (
            point == "manifest_write"
            and record
            and record.get("rotation", {}).get("state") == "completed"
        ):
            raise RuntimeError("persistence interrupted")
        original_write(path, raw)

    monkeypatch.setattr(store, "_atomic_write", failing_write)
    if point == "projection":

        def interrupted_project(identity):
            if identity.rotation and identity.rotation["state"] == "completed":
                raise RuntimeError("persistence interrupted")
            original_project(identity)

        monkeypatch.setattr(client, "_project", interrupted_project)
    with pytest.raises(RuntimeError, match="persistence interrupted"):
        asyncio.run(CommunityRotation(client).run(prepare=True))
    db.rollback()
    captured = store.read_identity_backup_snapshot(root, identity_home(root).root)
    restarted = store.load_or_create_installation_identity(identity_home(root))
    assert captured.key_id == restarted.key_id
    if point != "projection":
        assert restarted.key_id == old.key_id and restarted.pending_replacement
    monkeypatch.setattr(store, "_atomic_write", original_write)
    monkeypatch.setattr(client, "_project", original_project)
    asyncio.run(CommunityRotation(client).run())
    assert client.status()["pendingRotation"] is None
    assert (
        only_connection(db, "https://community.test").key_id == client.status()["identity"]["keyId"]
    )


def test_pending_blocks_other_identity_mutations_and_overlapping_probes(context):
    db, root = context
    prepared = prepare_rotation(identity_home(root), "community.test")
    client = make_client(db, root, lambda _: pytest.fail("must not send"))
    with pytest.raises(CommunityFailure, match="rotation_pending"):
        client.start()
    with pytest.raises(CommunityFailure, match="rotation_pending"):
        asyncio.run(client.rename("Another Name", "rename-1"))
    from scanner.security.private_identity_files import locked, private_directory

    with locked(private_directory(identity_home(root)), lock_name="rotation.lock", blocking=False):
        with pytest.raises(IdentityUnavailable, match="Another identity operation"):
            asyncio.run(CommunityRotation(client).run())
    assert (
        store.load_or_create_installation_identity(identity_home(root)).rotation
        == prepared.rotation
    )


@pytest.mark.parametrize("code", ["key_inactive", "membership_ineligible", "operation_conflict"])
def test_reconciliation_failure_keeps_original_candidate(context, code):
    db, root = context
    prepared = prepare_rotation(identity_home(root), "community.test")
    client = make_client(db, root, lambda _: httpx.Response(401, json={"error": {"code": code}}))
    with pytest.raises(CommunityFailure, match=code):
        asyncio.run(CommunityRotation(client).run(resend=True))
    assert (
        store.load_or_create_installation_identity(identity_home(root)).rotation
        == prepared.rotation
    )


def test_malformed_result_never_activates_and_second_rotation_uses_opposite_slot(context):
    db, root = context

    def malformed(_):
        response = committed(root)
        response["result"]["contributorId"] = "foreign-contributor"
        return httpx.Response(200, json=response)

    with pytest.raises(IdentityUnavailable):
        asyncio.run(CommunityRotation(make_client(db, root, malformed)).run(prepare=True))
    pending = store.load_or_create_installation_identity(identity_home(root))
    assert pending.pending_replacement
    client = make_client(db, root, lambda _: httpx.Response(200, json=committed(root)))
    asyncio.run(CommunityRotation(client).run())
    first = store.load_or_create_installation_identity(identity_home(root))
    assert first.key_file == "pending.key"
    asyncio.run(CommunityRotation(client).run(prepare=True))
    second = store.load_or_create_installation_identity(identity_home(root))
    assert second.key_file == "current.key" and second.key_id != first.key_id
    assert len(store.read_identity_backup_snapshot(root, identity_home(root).root).files) == 2
