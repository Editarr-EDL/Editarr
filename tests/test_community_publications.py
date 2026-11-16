"""Publication recovery uses exact saved buffers, never a rebuilt source."""

import asyncio
import base64
import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityOperationRow,
    CommunityPublicationRow,
    ScanRow,
)
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.donation_sources import SourceRef
from scanner.integrations.community.donations import prepare_donation
from scanner.integrations.community.publications import PublicationOutbox
from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
from scanner.persistence.repository.repository_community import (
    set_connection,
)
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from tests.community_identity_support import client_home, only_connection, only_identity_ref
from tests.test_community_donations import BOUNDS, options
from tests.test_community_donations import source_database as source_database

SERVICE = "https://community.example"


def setup_outbox(engine, tmp_path, handler):
    with Session(engine) as db:
        home = client_home(db, tmp_path)
    identity = load_or_create_installation_identity(home)
    save_identity_registration(
        home,
        contributor_id="contributor-1",
        service_url=SERVICE,
        last_known_key_state="active",
    )
    with Session(engine) as db:
        set_connection(
            db,
            credential_ref=only_identity_ref(db),
            service_url=SERVICE,
            contributor_id="contributor-1",
            installation_id=identity.installation_id,
            key_id=identity.key_id,
            sharing_enabled=True,
            consent_version="test-consent",
            consented_at="2026-10-03",
        )
        db.commit()
    prepared = prepare_donation(
        engine,
        SourceRef("scan", "scan"),
        replace(options(), key_id=identity.key_id),
        bounds=BOUNDS,
        source_limit=4,
    )
    db = Session(engine)
    transport = CommunityTransport(
        ServiceEndpoint(SERVICE, "community.example"),
        adapter=httpx.MockTransport(handler),
        delay=no_wait,
    )
    outbox = PublicationOutbox(
        CommunityClient(db, client_home(db, tmp_path), transport), request_bytes=100_000
    )
    outbox.queue(
        prepared,
        consent_token=prepared.consent_token,
        consent_version="test-consent",
        operation_id="publish-1",
        contribution_id="root-1",
        scan_id="scan",
    )
    return outbox, prepared


async def no_wait(_seconds):
    pass


def result(body):
    import hashlib

    return {
        "version": 1,
        "state": "completed",
        "publicationState": "published",
        "result": {
            "version": 1,
            "operationId": "publish-1",
            "contributionId": "root-1",
            "revisionId": body["submissionId"],
            "committedAt": 123,
            "artifactDigest": hashlib.sha256(base64.b64decode(body["artifact"])).hexdigest(),
        },
    }


def test_response_loss_reconciles_after_restart_without_rebuilding(source_database, tmp_path):
    saved = None
    posts = []
    signatures = []

    def handler(request):
        nonlocal saved
        if request.method == "GET":
            return (
                httpx.Response(200, json=saved)
                if saved
                else httpx.Response(404, json={"error": {"code": "operation_not_found"}})
            )
        posts.append(request.content)
        signatures.append(request.headers["signature-input"])
        assert request.headers["idempotency-key"] == "publish-1"
        saved = result(json.loads(request.content))
        raise httpx.ReadError("lost committed response")

    outbox, prepared = setup_outbox(source_database, tmp_path, handler)
    assert asyncio.run(outbox.run("publish-1", resend=True))["state"] == "paused"
    assert len(posts) == 3 and len(set(posts)) == 1
    assert len(set(signatures)) == 3
    sent = json.loads(posts[0])
    assert base64.b64decode(sent["artifact"]) == prepared.artifact_bytes
    assert base64.b64decode(sent["manifest"]) == prepared.manifest_bytes
    outbox.db.close()
    with Session(source_database) as db:
        db.get(ScanRow, "scan").name = "changed after approval"
        db.commit()
    outbox.db = outbox.client.db = Session(source_database)
    assert asyncio.run(outbox.run("publish-1", resend=True))["state"] == "published"
    assert len(posts) == 3
    assert (
        outbox.db.get(CommunityPublicationRow, "publish-1").artifact_bytes
        == prepared.artifact_bytes
    )
    outbox.db.close()


def test_authored_review_queues_source_without_arbitrary_scan_link(source_database, tmp_path):
    from tests.test_community_donations import seed_mixed_review

    outbox, _ = setup_outbox(
        source_database, tmp_path, lambda _: pytest.fail("queue must not send")
    )
    seed_mixed_review(source_database)
    identity = load_or_create_installation_identity(outbox.client.home)
    prepared = prepare_donation(
        source_database,
        SourceRef("review", "review"),
        replace(
            options(),
            key_id=identity.key_id,
            submission_id="manual-submission",
            duration_seconds=20,
            audio_source_scan_id="scan",
        ),
        bounds=BOUNDS,
        source_limit=4,
    )
    assert len(prepared.source.data()["scans"]) == 2
    outcome = outbox.queue(
        prepared,
        consent_token=prepared.consent_token,
        consent_version="test-consent",
        operation_id="manual-publish",
        contribution_id="manual-root",
    )
    assert outcome["state"] == "pending"
    assert outbox.db.get(CommunityOperationRow, "manual-publish").scan_id is None
    payload = outbox.db.get(CommunityPublicationRow, "manual-publish")
    assert (payload.source_kind, payload.source_id) == ("review", "review")
    outbox.db.close()


@pytest.mark.parametrize(
    "code,status,expected_state",
    [
        ("membership_ineligible", 403, "paused"),
        ("service_unavailable", 503, "paused"),
        ("invalid_artifact", 400, "rejected"),
        ("sharing_permission_required", 400, "rejected"),
        ("source_permission_unknown", 400, "rejected"),
        ("source_permission_incompatible", 400, "rejected"),
        ("source_attribution_mismatch", 400, "rejected"),
    ],
)
def test_failures_retain_exact_evidence(source_database, tmp_path, code, status, expected_state):
    posts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "operation_not_found"}})
        posts.append(request.content)
        return httpx.Response(status, json={"error": {"code": code}})

    outbox, prepared = setup_outbox(source_database, tmp_path, handler)
    outcome = asyncio.run(outbox.run("publish-1", resend=True))
    assert outcome["state"] == expected_state
    assert outcome["errorCode"] == code
    assert (
        outbox.db.get(CommunityPublicationRow, "publish-1").artifact_bytes
        == prepared.artifact_bytes
    )
    assert outbox.db.query(CommunityOperationRow).count() == 1
    outbox.db.rollback()
    if expected_state == "rejected":
        assert asyncio.run(outbox.run("publish-1", resend=True))["state"] == "rejected"
        assert len(posts) == 1
    outbox.db.close()


def test_cancel_before_send_is_local_and_preserves_payload(source_database, tmp_path):
    def handler(request):
        pytest.fail("cancelled pending publication must not send")

    outbox, _ = setup_outbox(source_database, tmp_path, handler)
    assert outbox.cancel("publish-1")["state"] == "cancelled"
    assert asyncio.run(outbox.run("publish-1", resend=True))["state"] == "cancelled"
    assert outbox.db.get(CommunityPublicationRow, "publish-1") is not None
    outbox.db.close()


def test_disconnect_holds_publication_and_reconnect_resumes_saved_payload(
    source_database, tmp_path
):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "operation_not_found"}})
        return httpx.Response(200, json=result(json.loads(request.content)))

    outbox, prepared = setup_outbox(source_database, tmp_path, handler)
    try:
        outbox.client.disconnect()
        outbox.db.rollback()
        assert asyncio.run(outbox.run("publish-1", resend=True))["state"] == "paused"
        assert calls == []
        operation = outbox.db.get(CommunityOperationRow, "publish-1")
        assert operation.state == "pending"
        assert (
            outbox.db.get(CommunityPublicationRow, "publish-1").artifact_bytes
            == prepared.artifact_bytes
        )
        outbox.db.rollback()
        outbox.client.start()
        outbox.db.rollback()
        assert asyncio.run(outbox.run("publish-1", resend=True))["state"] == "published"
        assert [request.method for request in calls] == ["GET", "POST"]
        assert (
            base64.b64decode(json.loads(calls[-1].content)["artifact"]) == prepared.artifact_bytes
        )
    finally:
        outbox.db.close()


@pytest.mark.parametrize("disconnect_during", ["lookup", "send"])
def test_disconnect_during_publication_stops_subsequent_requests(
    source_database, tmp_path, disconnect_during
):
    calls = []
    outbox = None

    def handler(request):
        calls.append(request.method)
        phase = "lookup" if request.method == "GET" else "send"
        if phase == disconnect_during:
            with Session(source_database) as db:
                CommunityClient(db, outbox.client.home, outbox.client.transport).disconnect()
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "operation_not_found"}})
        raise httpx.ReadError("response lost after disconnect")

    outbox, prepared = setup_outbox(source_database, tmp_path, handler)
    try:
        outcome = asyncio.run(outbox.run("publish-1", resend=True))
        assert calls == (["GET"] if disconnect_during == "lookup" else ["GET", "POST"])
        assert outcome["state"] == "paused"
        assert outcome["errorCode"] == "not_connected"
        operation = outbox.db.get(CommunityOperationRow, "publish-1")
        assert operation.state == ("pending" if disconnect_during == "lookup" else "submitted")
        assert (
            outbox.db.get(CommunityPublicationRow, "publish-1").artifact_bytes
            == prepared.artifact_bytes
        )
    finally:
        outbox.db.close()


def test_cancel_during_committed_send_does_not_claim_remote_deletion(source_database, tmp_path):
    outbox = None
    observed = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "operation_not_found"}})
        with Session(source_database) as db:
            other = PublicationOutbox(
                CommunityClient(db, client_home(db, tmp_path), outbox.client.transport),
                request_bytes=100_000,
            )
            observed.append(other.cancel("publish-1")["state"])
        return httpx.Response(200, json=result(json.loads(request.content)))

    outbox, _ = setup_outbox(source_database, tmp_path, handler)
    outcome = asyncio.run(outbox.run("publish-1", resend=True))
    assert observed == ["cancel_pending"]
    assert outcome["state"] == "published" and outcome["cancelRequested"]
    assert outbox.cancel("publish-1")["state"] == "published"
    outbox.db.close()


def test_crash_before_local_ack_recovers_service_result(source_database, tmp_path, monkeypatch):
    saved = None
    posts = []

    def handler(request):
        nonlocal saved
        if request.method == "GET":
            return (
                httpx.Response(200, json=saved)
                if saved
                else httpx.Response(404, json={"error": {"code": "operation_not_found"}})
            )
        posts.append(request.content)
        saved = result(json.loads(request.content))
        return httpx.Response(200, json=saved)

    outbox, _ = setup_outbox(source_database, tmp_path, handler)
    commit = outbox.db.commit

    def fail_ack():
        if outbox.db.get(CommunityOperationRow, "publish-1").state == "acknowledged":
            raise RuntimeError("crash before local acknowledgment")
        commit()

    monkeypatch.setattr(outbox.db, "commit", fail_ack)
    with pytest.raises(RuntimeError, match="local acknowledgment"):
        asyncio.run(outbox.run("publish-1", resend=True))
    outbox.db.close()
    outbox.db = outbox.client.db = Session(source_database)
    assert asyncio.run(outbox.run("publish-1"))["state"] == "published"
    assert len(posts) == 1
    outbox.db.close()


@pytest.mark.parametrize("damage", ["consent", "bytes", "bounds", "disabled"])
def test_changed_consent_or_corrupt_evidence_cannot_upload(source_database, tmp_path, damage):
    requests = []

    def handler(request):
        requests.append(request.method)
        return httpx.Response(404, json={"error": {"code": "operation_not_found"}})

    outbox, _ = setup_outbox(source_database, tmp_path, handler)
    with Session(source_database) as db:
        if damage == "consent":
            only_connection(db, SERVICE).consent_version = "new-consent"
        elif damage == "bytes":
            db.get(CommunityPublicationRow, "publish-1").artifact_bytes += b"corrupt"
        db.commit()
    if damage == "bounds":
        outbox.request_bytes = 10
    elif damage == "disabled":
        outbox.request_bytes = None
    outcome = asyncio.run(outbox.run("publish-1", resend=True))
    assert requests == ["GET"]
    assert outcome["state"] in {"paused", "rejected"}
    assert (
        outcome["errorCode"]
        == {
            "consent": "sharing_consent_required",
            "bytes": "publication_evidence_invalid",
            "bounds": "publication_limit",
            "disabled": "publication_not_configured",
        }[damage]
    )
    outbox.db.close()


def test_task_cancellation_preserves_uncertain_submitted_state(source_database, tmp_path):
    async def handler(request):
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "operation_not_found"}})
        raise asyncio.CancelledError()

    outbox, _ = setup_outbox(source_database, tmp_path, handler)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(outbox.run("publish-1", resend=True))
    assert outbox.status("publish-1")["phase"] == "submitted"
    assert outbox.cancel("publish-1")["state"] == "cancel_pending"
    outbox.db.close()


def test_status_check_never_uploads_and_legacy_payloads_fail_closed(source_database, tmp_path):
    requests = []

    def handler(request):
        requests.append(request.method)
        return httpx.Response(404, json={"error": {"code": "operation_not_found"}})

    outbox, _ = setup_outbox(source_database, tmp_path, handler)
    assert asyncio.run(outbox.run("publish-1"))["state"] == "pending"
    assert requests == ["GET"]
    with Session(source_database) as db:
        db.delete(db.get(CommunityPublicationRow, "publish-1"))
        db.commit()
    from scanner.integrations.community.transport import CommunityFailure

    with pytest.raises(CommunityFailure, match="evidence_unavailable"):
        asyncio.run(outbox.run("publish-1", resend=True))
    assert requests == ["GET"]
    outbox.db.close()
