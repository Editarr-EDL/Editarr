"""Recovery uses temporary identities, SQLite, and mocked hosted outcomes only."""

import asyncio
import json

import httpx
import pytest

from scanner.integrations.community.recovery import CommunityRecovery
from scanner.integrations.community.transport import CommunityFailure
from scanner.security import community_identity as store
from scanner.security.community_key_recovery import recovery_source
from scanner.security.community_key_rotation import prepare_rotation
from scanner.security.private_identity_files import IdentityUnavailable
from tests.community_identity_support import identity_home, only_connection
from tests.test_community_rotation import context as context
from tests.test_community_rotation import make_client


def outcome(root):
    identity = recovery_source(identity_home(root))
    record = identity.recovery
    return {
        "version": 1,
        "state": "completed",
        "result": {
            "version": 1,
            "kind": "key_recovery",
            "operationId": record["operationId"],
            "stateId": record["stateId"],
            "contributorId": identity.contributor_id,
            "installationId": identity.installation_id,
            "oldKeyId": record["oldKeyId"],
            "newKeyId": record["newKeyId"],
            "committedAt": 1700000030000,
        },
    }


def recovery(db, root, handler):
    return CommunityRecovery(make_client(db, root, handler))


def test_missing_old_key_recovers_only_after_verified_commit(context):
    db, root = context
    old = store.load_or_create_installation_identity(identity_home(root))
    (identity_home(root).root / old.key_file).unlink()
    requests = []

    def hosted(request):
        requests.append(request)
        if request.url.path.endswith("recovery-status"):
            return httpx.Response(
                200, json={"version": 1, "state": "not_committed", "result": None}
            )
        return httpx.Response(200, json=outcome(root))

    workflow = recovery(db, root, hosted)
    assert workflow.status()["identity"]["keyId"] == old.key_id
    assert not requests
    started = workflow.start(old.key_id)
    assert "/v1/discord/recovery-start?" in started["authorizationUrl"]
    candidate = recovery_source(identity_home(root)).recovery
    assert candidate["candidatePrivateKey"] not in json.dumps(started)
    assert candidate["secret"] not in json.dumps(started)
    with pytest.raises(IdentityUnavailable, match="needs recovery"):
        store.load_or_create_installation_identity(identity_home(root))
    result = asyncio.run(workflow.run(state_id="b" * 64, resend=True))
    assert result["completed"] and result["pending"] is None
    assert len({request.content for request in requests}) == 1
    active = store.load_or_create_installation_identity(identity_home(root))
    assert active.contributor_id == old.contributor_id
    assert active.key_id == candidate["newKeyId"]
    assert only_connection(db, old.service_url).key_id == active.key_id
    assert active.recovery["candidatePrivateKey"] is None
    assert prepare_rotation(identity_home(root), "community.test").recovery is None


@pytest.mark.parametrize("key_state", ["retired", "revoked", "compromised"])
def test_candidate_proof_recovers_inactive_identity_without_ordinary_authority(context, key_state):
    db, root = context
    old = recovery_source(identity_home(root))
    requests = []

    def hosted(request):
        requests.append(request)
        if request.url.path.endswith("recovery-status"):
            return httpx.Response(
                200, json={"version": 1, "state": "not_committed", "result": None}
            )
        assert request.url.path == "/v1/keys/recover"
        return httpx.Response(200, json=outcome(root))

    workflow = recovery(db, root, hosted)
    workflow.start(old.key_id)
    store.save_identity_registration(
        identity_home(root),
        contributor_id=old.contributor_id,
        service_url=old.service_url,
        last_known_key_state=key_state,
    )
    inactive = recovery_source(identity_home(root))

    with pytest.raises(CommunityFailure, match="recovery_required"):
        asyncio.run(workflow.client.transport.request("GET", "/v1/identity", identity=inactive))
    with pytest.raises(CommunityFailure, match="recovery_required"):
        asyncio.run(
            workflow.client.transport.request(
                "GET", "/v1/keys/recovery-status", identity=inactive, candidate_proof=True
            )
        )
    assert not requests

    result = asyncio.run(workflow.run(state_id="b" * 64, resend=True))
    assert result["completed"]
    assert [request.url.path for request in requests] == [
        "/v1/keys/recovery-status",
        "/v1/keys/recover",
    ]
    candidate_key_id = recovery_source(identity_home(root)).recovery["newKeyId"]
    assert all(candidate_key_id in request.headers["signature-input"] for request in requests)
    assert recovery_source(identity_home(root)).last_known_key_state == "active"


def test_inactive_candidate_proof_reconciles_operation_lookup(context):
    db, root = context
    old = recovery_source(identity_home(root))
    requests = []
    committed = None

    def hosted(request):
        requests.append(request)
        if request.url.path.endswith("recovery-status"):
            return httpx.Response(409, json={"error": {"code": "operation_conflict"}})
        assert request.url.path.endswith("/key_recovery/" + committed["operationId"])
        return httpx.Response(200, json={"state": "completed", "result": committed})

    workflow = recovery(db, root, hosted)
    workflow.start(old.key_id)
    from scanner.security.community_key_recovery import bind_recovery

    bind_recovery(identity_home(root), "b" * 64)
    committed = outcome(root)["result"]
    store.save_identity_registration(
        identity_home(root),
        contributor_id=old.contributor_id,
        service_url=old.service_url,
        last_known_key_state="revoked",
    )

    result = asyncio.run(workflow.run(resend=True))

    assert result["completed"]
    assert [request.method for request in requests] == ["POST", "GET"]
    assert [request.url.path for request in requests] == [
        "/v1/keys/recovery-status",
        "/v1/key-operations/key_recovery/" + committed["operationId"],
    ]


@pytest.mark.parametrize(
    ("code", "status_code"), [("recovery_expired", 409), ("invalid_recovery", 400)]
)
def test_http_recovery_status_error_reconciles_committed_operation(context, code, status_code):
    db, root = context
    old = recovery_source(identity_home(root))
    requests = []
    committed = None

    def hosted(request):
        requests.append(request)
        if request.url.path.endswith("recovery-status"):
            return httpx.Response(status_code, json={"error": {"code": code}})
        assert request.method == "GET"
        assert request.url.path.endswith("/key_recovery/" + committed["operationId"])
        return httpx.Response(200, json={"state": "completed", "result": committed})

    workflow = recovery(db, root, hosted)
    workflow.start(old.key_id)
    from scanner.security.community_key_recovery import bind_recovery

    bind_recovery(identity_home(root), "b" * 64)
    committed = outcome(root)["result"]

    result = asyncio.run(workflow.run(resend=True))

    assert result["completed"]
    assert [request.method for request in requests] == ["POST", "GET"]
    assert [request.url.path for request in requests] == [
        "/v1/keys/recovery-status",
        "/v1/key-operations/key_recovery/" + committed["operationId"],
    ]
    assert all(
        recovery_source(identity_home(root)).recovery["newKeyId"]
        in request.headers["signature-input"]
        for request in requests
    )


def test_lost_response_reuses_candidate_operation_and_exact_body(context):
    db, root = context
    old = recovery_source(identity_home(root))
    requests = []

    def lost(request):
        requests.append(request)
        if request.url.path.endswith("recovery-status"):
            return httpx.Response(
                200, json={"version": 1, "state": "not_committed", "result": None}
            )
        raise httpx.ReadError("lost committed response", request=request)

    workflow = recovery(db, root, lost)
    workflow.start(old.key_id)
    with pytest.raises(CommunityFailure, match="network_unavailable"):
        asyncio.run(workflow.run(state_id="b" * 64, resend=True))
    pending = recovery_source(identity_home(root)).recovery
    assert workflow.status()["pending"]["operationId"] == pending["operationId"]
    assert workflow.start(old.key_id)["pending"]["newKeyId"] == pending["newKeyId"]

    def probe(request):
        requests.append(request)
        assert request.url.path.endswith("recovery-status")
        return httpx.Response(200, json=outcome(root))

    result = asyncio.run(recovery(db, root, probe).run())
    assert result["completed"]
    assert len({request.content for request in requests}) == 1
    assert len({request.headers["idempotency-key"] for request in requests}) == 1


@pytest.mark.parametrize(
    "kind", ["contributorId", "installationId", "newKeyId", "operationId", "stateId", "version"]
)
def test_foreign_or_malformed_outcome_preserves_private_journal(context, kind):
    db, root = context
    old = recovery_source(identity_home(root))

    def malformed(request):
        response = outcome(root)
        response["result"][kind] = "wrong"
        return httpx.Response(200, json=response)

    workflow = recovery(db, root, malformed)
    workflow.start(old.key_id)
    with pytest.raises(IdentityUnavailable):
        asyncio.run(workflow.run(state_id="b" * 64, resend=True))
    assert recovery_source(identity_home(root)).recovery["state"] == "pending"
    assert recovery_source(identity_home(root)).key_id == old.key_id


def test_activation_fault_and_projection_fault_reconcile_same_generation(context, monkeypatch):
    db, root = context
    old = recovery_source(identity_home(root))
    workflow = recovery(db, root, lambda request: httpx.Response(200, json=outcome(root)))
    workflow.start(old.key_id)
    from scanner.security import community_key_recovery as journal

    atomic = journal._atomic_write

    def fail_manifest(path, contents):
        if path.name == "identity.json" and b'"state":"completed"' in contents:
            raise IdentityUnavailable("unavailable", "synthetic publication failure")
        atomic(path, contents)

    monkeypatch.setattr(journal, "_atomic_write", fail_manifest)
    with pytest.raises(IdentityUnavailable):
        asyncio.run(workflow.run(state_id="b" * 64, resend=True))
    candidate = recovery_source(identity_home(root)).recovery["newKeyId"]
    monkeypatch.setattr(journal, "_atomic_write", atomic)
    monkeypatch.setattr(
        workflow.client,
        "_project",
        lambda identity: (_ for _ in ()).throw(RuntimeError("projection fault")),
    )
    with pytest.raises(RuntimeError, match="projection fault"):
        asyncio.run(workflow.run())
    assert recovery_source(identity_home(root)).key_id == candidate
    assert asyncio.run(
        recovery(db, root, lambda request: pytest.fail("completed status must be local")).run()
    )["completed"]
    assert only_connection(db, old.service_url).key_id == candidate


def test_pending_recovery_freezes_enrollment_rename_and_rotation(context):
    db, root = context
    client = make_client(db, root, lambda request: pytest.fail("No competing send permitted"))
    recovery(db, root, lambda request: pytest.fail("status must be local")).start(
        recovery_source(identity_home(root)).key_id
    )
    assert client.status()["pendingRecovery"]
    with pytest.raises(CommunityFailure, match="recovery_pending"):
        client.start()
    with pytest.raises(CommunityFailure, match="recovery_pending"):
        asyncio.run(client.rename("Another Name", "operation"))


def test_expired_uncommitted_authorization_keeps_candidate_for_fresh_discord(context):
    db, root = context
    workflow = recovery(
        db, root, lambda request: httpx.Response(409, json={"error": {"code": "recovery_expired"}})
    )
    old_key = recovery_source(identity_home(root)).key_id
    workflow.start(old_key)
    with pytest.raises(CommunityFailure):
        asyncio.run(workflow.run(state_id="b" * 64, resend=True))
    previous = recovery_source(identity_home(root)).recovery
    fresh = workflow.start(old_key)
    assert fresh["authorizationUrl"]
    assert recovery_source(identity_home(root)).recovery["newKeyId"] == previous["newKeyId"]
    assert recovery_source(identity_home(root)).recovery["operationId"] == previous["operationId"]

    def hosted(request):
        if request.url.path.endswith("recovery-status"):
            return httpx.Response(
                200, json={"version": 1, "state": "not_committed", "result": None}
            )
        return httpx.Response(200, json=outcome(root))

    assert asyncio.run(recovery(db, root, hosted).run(state_id="c" * 64, resend=True))["completed"]


def test_fresh_grant_conflict_reads_existing_candidate_operation_without_replacing_again(context):
    db, root = context
    workflow = recovery(db, root, lambda request: httpx.Response(200, json=outcome(root)))
    old_key = recovery_source(identity_home(root)).key_id
    workflow.start(old_key)
    from scanner.security.community_key_recovery import bind_recovery

    bind_recovery(identity_home(root), "b" * 64)
    original = outcome(root)

    def hosted(request):
        if request.url.path.endswith("recovery-status"):
            return httpx.Response(409, json={"error": {"code": "operation_conflict"}})
        assert request.method == "GET" and "/key_recovery/" in request.url.path
        return httpx.Response(200, json={"state": "completed", "result": original["result"]})

    result = asyncio.run(recovery(db, root, hosted).run(state_id="c" * 64, resend=True))
    assert result["completed"]
    assert recovery_source(identity_home(root)).recovery["result"]["stateId"] == "b" * 64


def test_conflicting_database_identity_blocks_preparation_without_overwriting_metadata(context):
    db, root = context
    old = recovery_source(identity_home(root))
    row = only_connection(db, old.service_url)
    row.installation_id = "another-installation"
    db.commit()
    workflow = recovery(db, root, lambda request: pytest.fail("No network allowed"))
    with pytest.raises(IdentityUnavailable, match="does not match"):
        workflow.start(old.key_id)
    assert recovery_source(identity_home(root)).recovery is None


def test_private_request_and_outcome_match_shared_recovery_contract(context):
    from pathlib import Path
    from urllib.parse import parse_qs, urlsplit

    from scanner.security.community_key_recovery import bind_recovery

    db, root = context
    fixture = Path(__file__).parent / "fixtures/community/key-recovery-v1.json"
    contract = json.loads(fixture.read_text())
    workflow = recovery(db, root, lambda request: pytest.fail("Contract inspection is offline"))
    started = workflow.start(recovery_source(identity_home(root)).key_id)
    target = urlsplit(started["authorizationUrl"])
    assert target.path == contract["routes"]["start"]
    assert set(parse_qs(target.query)) == set(contract["startFields"])
    identity = bind_recovery(identity_home(root), "b" * 64)
    assert list(json.loads(identity.recovery["body"])) == contract["bodyFields"]
    assert set(outcome(root)) == set(contract["envelopeFields"])
    assert set(outcome(root)["result"]) == set(contract["resultFields"])
