import asyncio
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import Base, CommunityConnectionRow
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.security.community_identity import load_or_create_installation_identity
from tests.community_identity_support import client_home, identity_home


@pytest.fixture
def context(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db, tmp_path
    engine.dispose()


def profile(identity, *, name="Cedar Frost Grove", revision=1):
    return {
        "contributorId": "contributor-001",
        "shortId": "contribu",
        "installationId": identity.installation_id,
        "keyId": identity.key_id,
        "keyState": "active",
        "displayName": name,
        "profileRevision": revision,
        "policyVersion": "test-v1",
        "protocolVersion": 1,
        "allowedOperations": ["identity:read", "identity:rename"],
    }


def client(context, handle, **kwargs):
    db, root = context
    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(handle),
        **kwargs,
    )
    return CommunityClient(db, client_home(db, root), transport)


def test_start_persists_secret_before_browser_url_and_restart_reuses_it(context):
    db, root = context
    service = client(context, lambda _: pytest.fail("start must use browser cookie flow"))
    started = service.start()
    identity = load_or_create_installation_identity(identity_home(root))
    query = parse_qs(urlsplit(started["authorizationUrl"]).query)
    secret = identity.client_state["pending"]["secret"]
    assert query["confirmationHash"] == [
        hashlib.sha256(("editarr-enrollment-confirmation-v1:" + secret).encode()).hexdigest()
    ]
    assert secret not in json.dumps(started)
    assert service.start()["authorizationUrl"] == started["authorizationUrl"]
    assert db.query(CommunityConnectionRow).count() == 0


def test_completion_lost_response_restart_and_atomic_registration(context):
    _, root = context
    requests = []
    identity = load_or_create_installation_identity(identity_home(root))

    def unavailable(request):
        requests.append(request)
        raise httpx.ReadError("private provider error", request=request)

    async def delay(_):
        return None

    service = client(context, unavailable, delay=delay)
    service.start()
    state_id = "b" * 64
    with pytest.raises(CommunityFailure, match="network_unavailable"):
        asyncio.run(service.complete(state_id))
    pending = load_or_create_installation_identity(identity_home(root)).client_state["pending"]
    assert pending["state_id"] == state_id
    with pytest.raises(CommunityFailure, match="handoff_conflict"):
        asyncio.run(service.complete("c" * 64))

    def reconciled(request):
        requests.append(request)
        return httpx.Response(200, json=profile(identity) | {"discordId": "must-not-escape"})

    restarted = client(context, reconciled)
    result = asyncio.run(restarted.complete(state_id))
    assert len({request.content for request in requests}) == 1
    assert len({request.headers["signature-input"] for request in requests}) == 4
    assert result["connected"] and result["pending"] is None
    assert "must-not-escape" not in json.dumps(result)
    assert load_or_create_installation_identity(identity_home(root)).client_state["pending"] is None
    with pytest.raises(CommunityFailure, match="enrollment_expired"):
        asyncio.run(restarted.complete(state_id))


def test_refresh_replaces_name_and_refuses_wrong_key_and_stale_revision(context):
    _, root = context
    identity = load_or_create_installation_identity(identity_home(root))
    response = profile(identity)
    service = client(context, lambda _: httpx.Response(200, json=response))
    service.start()
    asyncio.run(service.complete("b" * 64))
    response.update(displayName="Updated Public Name", profileRevision=2)
    assert asyncio.run(service.refresh())["profile"]["displayName"] == "Updated Public Name"
    response.update(profileRevision=1)
    with pytest.raises(CommunityFailure, match="stale_profile"):
        asyncio.run(service.refresh())
    response.update(keyId="wrong-key", profileRevision=3)
    with pytest.raises(CommunityFailure, match="invalid_response"):
        asyncio.run(service.refresh())


def test_file_registration_recovers_interrupted_database_projection(context, monkeypatch):
    db, root = context
    identity = load_or_create_installation_identity(identity_home(root))
    service = client(context, lambda _: httpx.Response(200, json=profile(identity)))
    service.start()
    original = service._project
    monkeypatch.setattr(
        service, "_project", lambda _: (_ for _ in ()).throw(RuntimeError("interrupted"))
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        asyncio.run(service.complete("b" * 64))
    db.rollback()
    assert (
        load_or_create_installation_identity(identity_home(root)).contributor_id
        == "contributor-001"
    )
    monkeypatch.setattr(service, "_project", original)
    assert service.status()["connected"]
    assert db.query(CommunityConnectionRow).count() == 1


def test_rename_intent_survives_cancellation_and_restart(context):
    _, root = context
    identity = load_or_create_installation_identity(identity_home(root))
    requests = []
    service = client(context, lambda _: httpx.Response(200, json=profile(identity)))
    service.start()
    asyncio.run(service.complete("b" * 64))

    def cancelled(request):
        requests.append(request)
        raise asyncio.CancelledError

    interrupted = client(context, cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(interrupted.rename("New Public Name", "rename-1"))
    assert interrupted.status()["pendingRename"]["operation_id"] == "rename-1"
    with pytest.raises(CommunityFailure, match="operation_conflict"):
        asyncio.run(interrupted.rename("Different Name", "rename-1"))

    def accepted(request):
        requests.append(request)
        return httpx.Response(200, json=profile(identity, name="New Public Name", revision=2))

    restarted = client(context, accepted)
    result = asyncio.run(restarted.rename("New Public Name", "rename-1"))
    assert result["pendingRename"] is None
    assert result["profile"]["displayName"] == "New Public Name"
    assert requests[0].content == requests[1].content
    assert requests[0].headers["idempotency-key"] == requests[1].headers["idempotency-key"]


def test_expiry_and_cancel_clear_secret_without_changing_key(context):
    _, root = context
    service = client(context, lambda _: pytest.fail("must not send expired authority"))
    service.clock = lambda: 1000
    service.start()
    identity = load_or_create_installation_identity(identity_home(root))
    service.clock = lambda: 2200
    assert service.status()["pending"] is None
    assert load_or_create_installation_identity(identity_home(root)).key_id == identity.key_id
    service.start()
    assert service.cancel()["pending"] is None
