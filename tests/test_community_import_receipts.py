"""Original receipt recovery and retries use temporary databases and synthetic HTTP."""

import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.orm import Session

from scanner.db import CommunityIdentityRow, CommunityImportRow, CommunityOperationRow
from scanner.integrations.community.downloads import verify_download_bundle
from scanner.integrations.community.receipts import ReceiptOutbox, acknowledge_committed_import
from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.persistence.repository.community_imports import import_verified_download
from scanner.persistence.repository.repository_community import set_connection
from scanner.security.community_download_receipts import (
    ReceiptFields,
    parse_receipt_keys,
    receipt_identity,
    receipt_signing_bytes,
)
from scanner.security.private_identity_files import IdentityHome
from tests.community_identity_support import only_identity_ref
from tests.test_community_downloads import identity, response
from tests.test_community_verified_import import (
    BOUNDS,
    RECEIPT_VECTOR,
    SERVICE,
    download_input,
    local_target,
    verified_input,
)
from tests.test_community_verified_import import import_database as import_database


def outbox(db, tmp_path, handle):
    from dataclasses import replace

    local_identity = replace(identity(tmp_path), installation_id="installation-reader")

    async def delay(seconds):
        pass

    client = SimpleNamespace(
        db=db,
        home=IdentityHome(tmp_path, only_identity_ref(db)),
        _identity=lambda: local_identity,
        transport=CommunityTransport(
            ServiceEndpoint(SERVICE, "community.example"),
            adapter=httpx.MockTransport(handle),
            delay=delay,
        ),
    )
    return ReceiptOutbox(client)


def committed(db, tmp_path):
    downloaded = verified_input()
    result = import_verified_download(
        db,
        downloaded,
        local_target(downloaded, tmp_path),
        bounds=BOUNDS,
        confirm_uncertain=True,
        credential_ref=only_identity_ref(db),
    )
    db.rollback()
    return result


def second_contributor(db, tmp_path, handle):
    sender = outbox(db, tmp_path, handle)
    reference = "b" * 32
    db.add(CommunityIdentityRow(credential_ref=reference))
    set_connection(
        db,
        credential_ref=reference,
        service_url=SERVICE,
        contributor_id="second-reader",
        installation_id="second-installation",
        key_id="sha256:second-reader",
    )
    db.commit()
    local_identity = replace(
        sender.client._identity(),
        contributor_id="second-reader",
        installation_id="second-installation",
    )
    sender.client.home = IdentityHome(tmp_path, reference)
    sender.client._identity = lambda: local_identity
    return sender


def test_recovery_ignores_another_contributors_receipt(import_database, tmp_path):
    calls = []
    with Session(import_database) as db:
        committed(db, tmp_path)
        db.query(CommunityOperationRow).delete()
        db.commit()
        sender = second_contributor(db, tmp_path, lambda request: calls.append(request))
        assert sender.recover() == []
        assert calls == []
        assert db.query(CommunityOperationRow).count() == 0


def test_shared_import_retains_each_contributors_exact_receipt(import_database, tmp_path):
    value, keys, _ = download_input()
    fields = value["receipt"]["receipt"]
    fields["contributorId"] = "second-reader"
    fields["receiptId"] = receipt_identity(
        SERVICE, "second-reader", fields["revisionId"], fields["artifactDigest"]
    )
    signer = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(RECEIPT_VECTOR["syntheticSeedHex"]))
    value["receipt"]["signature"] = base64.b64encode(
        signer.sign(receipt_signing_bytes(ReceiptFields.model_validate(fields)))
    ).decode()
    downloaded = verify_download_bundle(
        value,
        service=SERVICE,
        contribution_id="contribution-root",
        revision_id=value["revisionId"],
        contributor_id="second-reader",
        trusted_keys=parse_receipt_keys(keys),
        bounds=BOUNDS,
    )
    calls = []

    def handle(request):
        if request.url.path == "/v1/receipt-keys":
            return response({"version": 1, "service": SERVICE, "keys": keys})
        receipt = json.loads(request.content)["receipt"]["receipt"]
        calls.append(receipt["contributorId"])
        return response(
            {
                "version": 1,
                "state": "completed",
                "result": {
                    "version": 1,
                    "operationId": request.headers["idempotency-key"],
                    "revisionId": receipt["revisionId"],
                    "artifactDigest": receipt["artifactDigest"],
                    "receiptId": receipt["receiptId"],
                    "counted": True,
                },
            }
        )

    with Session(import_database) as db:
        first = committed(db, tmp_path)
        original = db.get(CommunityImportRow, first.import_id).receipt_json
        sender = second_contributor(db, tmp_path, handle)
        second = import_verified_download(
            db,
            downloaded,
            local_target(downloaded, tmp_path),
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=sender.client.home.credential_ref,
        )
        db.rollback()
        assert second.reused and second.scan.id == first.scan.id
        assert second.receipt_state == "pending"
        assert asyncio.run(sender.run(second.import_id))["state"] == "acknowledged"
        assert db.get(CommunityImportRow, first.import_id).receipt_json == original
        operations = (
            db.query(CommunityOperationRow).order_by(CommunityOperationRow.contributor_id).all()
        )
        assert [(row.contributor_id, row.state) for row in operations] == [
            ("reader", "pending"),
            ("second-reader", "acknowledged"),
        ]
    assert calls == ["second-reader"]


def test_retry_sends_original_saved_receipt_and_recovers_missing_intent(import_database, tmp_path):
    _, keys, _ = download_input()
    calls = []
    results = {}

    def handle(request):
        if request.url.path == "/v1/receipt-keys":
            return response({"version": 1, "service": SERVICE, "keys": keys})
        value = json.loads(request.content)
        receipt = value["receipt"]["receipt"]
        operation = request.headers["idempotency-key"]
        calls.append(request)
        result = {
            "version": 1,
            "operationId": operation,
            "revisionId": receipt["revisionId"],
            "artifactDigest": receipt["artifactDigest"],
            "receiptId": receipt["receiptId"],
            "counted": True,
        }
        results.setdefault(operation, result)
        if len(calls) <= 3:
            raise httpx.ReadError("lost response after remote commit")
        return response({"version": 1, "state": "completed", "result": results[operation]})

    with Session(import_database) as db:
        imported = committed(db, tmp_path)
        stored_receipt = json.loads(db.get(CommunityImportRow, imported.import_id).receipt_json)
        db.query(CommunityOperationRow).delete()
        db.commit()
        sender = outbox(db, tmp_path, handle)
        assert sender.recover(limit=1) == [imported.import_id]
        assert sender.recover(limit=1) == []
        with pytest.raises(CommunityFailure):
            asyncio.run(sender.run(imported.import_id))
        assert db.query(CommunityOperationRow).one().state == "submitted"
        db.rollback()
        assert asyncio.run(sender.run(imported.import_id))["state"] == "acknowledged"
        assert len(results) == 1
        assert all(json.loads(call.content)["receipt"] == stored_receipt for call in calls)
        assert len({call.headers["signature-input"] for call in calls}) == len(calls)
        assert len({call.headers["idempotency-key"] for call in calls}) == 1
        assert db.query(CommunityImportRow).count() == 1


@pytest.mark.parametrize(
    "fault", ["missing", "receipt", "bytes", "result", "wrong_contributor", "revoked"]
)
def test_absent_or_corrupt_evidence_and_invalid_response_cannot_acknowledge(
    import_database, tmp_path, fault
):
    _, keys, _ = download_input()
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/receipt-keys":
            return response({"version": 1, "service": SERVICE, "keys": keys})
        return response({"version": 1, "state": "completed", "result": {"counted": 1}})

    with Session(import_database) as db:
        imported = committed(db, tmp_path)
        row = db.get(CommunityImportRow, imported.import_id)
        if fault == "receipt":
            value = json.loads(row.receipt_json)
            value["signature"] = "A" * 88
            row.receipt_json = json.dumps(value)
            db.query(CommunityOperationRow).delete()
        elif fault == "bytes":
            row.artifact_bytes = b"corrupt"
        elif fault == "wrong_contributor":
            value = json.loads(row.receipt_json)
            value["receipt"]["contributorId"] = "someone-else"
            row.receipt_json = json.dumps(value)
            db.query(CommunityOperationRow).delete()
        elif fault == "revoked":
            keys[0]["status"] = "revoked"
        db.commit()
        sender = outbox(db, tmp_path, handle)
        with pytest.raises((CommunityFailure, ValueError)):
            asyncio.run(sender.run("missing" if fault == "missing" else imported.import_id))
        assert db.query(CommunityOperationRow).filter_by(state="acknowledged").count() == 0
        assert db.query(CommunityImportRow).count() == 1
        if fault != "result":
            assert "/v1/import-receipts" not in calls


def test_receipt_failure_preserves_successful_import_and_returns_durable_state(
    import_database, tmp_path
):
    def handle(request):
        return httpx.Response(503, json={"error": {"code": "service_unavailable"}})

    with Session(import_database) as db:
        imported = committed(db, tmp_path)
        sender = outbox(db, tmp_path, handle)
        assert (
            asyncio.run(acknowledge_committed_import(sender.client, imported.import_id, "pending"))
            == "pending"
        )
        assert db.query(CommunityImportRow).count() == 1
        assert db.query(CommunityOperationRow).one().state == "pending"


def test_restored_pending_queue_reuses_original_operation_and_receipt(import_database, tmp_path):
    import sqlite3

    from sqlalchemy import create_engine

    _, keys, _ = download_input()
    seen = set()
    bodies = []

    def handle(request):
        if request.url.path == "/v1/receipt-keys":
            return response({"version": 1, "service": SERVICE, "keys": keys})
        bodies.append(request.content)
        receipt = json.loads(request.content)["receipt"]["receipt"]
        operation = request.headers["idempotency-key"]
        seen.add(operation)
        return response(
            {
                "version": 1,
                "state": "completed",
                "result": {
                    "version": 1,
                    "operationId": operation,
                    "revisionId": receipt["revisionId"],
                    "artifactDigest": receipt["artifactDigest"],
                    "receiptId": receipt["receiptId"],
                    "counted": True,
                },
            }
        )

    restored_path = tmp_path / "restored-receipts.db"
    with Session(import_database) as db:
        imported = committed(db, tmp_path)
        with (
            sqlite3.connect(import_database.url.database) as original,
            sqlite3.connect(restored_path) as restored,
        ):
            original.backup(restored)
        assert (
            asyncio.run(outbox(db, tmp_path, handle).run(imported.import_id))["state"]
            == "acknowledged"
        )
    engine = create_engine(f"sqlite:///{restored_path}")
    try:
        with Session(engine) as db:
            assert (
                asyncio.run(outbox(db, tmp_path, handle).run(imported.import_id))["state"]
                == "acknowledged"
            )
            assert db.query(CommunityOperationRow).one().state == "acknowledged"
    finally:
        engine.dispose()
    assert len(seen) == 1 and bodies[0] == bodies[1]


def test_uncommitted_import_cannot_enter_sender(import_database, tmp_path):
    calls = []
    with Session(import_database) as db:
        imported = committed(db, tmp_path)
        db.get(CommunityImportRow, imported.import_id)
        sender = outbox(db, tmp_path, lambda request: calls.append(request))
        with pytest.raises(CommunityFailure, match="import_session_not_clean"):
            asyncio.run(sender.run(imported.import_id))
        assert calls == []


def test_shared_acknowledgment_contract_preserves_verified_receipt_and_stable_operation():
    from scanner.security.community_download_receipts import (
        parse_receipt_keys,
        verify_download_receipt,
    )

    contract = json.loads(
        (Path(__file__).parent / "fixtures/community/import-acknowledgment-v1.json").read_text()
    )
    value = contract["request"]["receipt"]["receipt"]
    verified = verify_download_receipt(
        contract["request"]["receipt"],
        service=SERVICE,
        contributor_id=value["contributorId"],
        revision_id=value["revisionId"],
        artifact_digest=value["artifactDigest"],
        trusted_keys=parse_receipt_keys(
            [
                {
                    "keyId": value["serviceKeyId"],
                    "publicKey": contract["publicKey"],
                    "status": "active",
                }
            ]
        ),
    )
    assert contract["operationId"] == "import-" + verified.receipt.receipt_id
    assert contract["response"] == {
        "version": 1,
        "state": "completed",
        "result": {
            "version": 1,
            "operationId": contract["operationId"],
            "revisionId": verified.receipt.revision_id,
            "artifactDigest": verified.receipt.artifact_digest,
            "receiptId": verified.receipt.receipt_id,
            "counted": True,
        },
    }
