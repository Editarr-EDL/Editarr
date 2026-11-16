"""Service-origin key refresh and outer-response integrity use synthetic HTTP only."""

import asyncio
import base64
import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy.orm import Session

from scanner.db import CommunityImportRow
from scanner.integrations.community.downloads import download_and_import
from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.security.community_identity import load_or_create_installation_identity
from tests.community_identity_support import identity_home, only_identity_ref
from tests.test_community_verified_import import (
    BOUNDS,
    SERVICE,
    download_input,
    local_target,
    verified_input,
)
from tests.test_community_verified_import import import_database as import_database


def response(value):
    data = json.dumps(value, separators=(",", ":")).encode()
    digest = base64.b64encode(hashlib.sha256(data).digest()).decode()
    return httpx.Response(
        200,
        content=data,
        headers={
            "content-type": "application/json",
            "content-length": str(len(data)),
            "content-digest": f"sha-256=:{digest}:",
        },
    )


def identity(tmp_path):
    return replace(
        load_or_create_installation_identity(identity_home(tmp_path)),
        contributor_id="reader",
        service_url=SERVICE,
    )


def test_each_import_refreshes_trust_and_reuses_local_evidence_without_stale_fallback(
    import_database, tmp_path
):
    bundle, keys, _ = download_input()
    calls = []

    def handle(request):
        calls.append(request)
        return response(
            {"version": 1, "service": SERVICE, "keys": keys}
            if request.url.path == "/v1/receipt-keys"
            else bundle
        )

    transport = CommunityTransport(
        ServiceEndpoint(SERVICE, "community.example"), adapter=httpx.MockTransport(handle)
    )
    local_identity = identity(tmp_path)
    target = local_target(verified_input(), tmp_path)
    with Session(import_database) as db:
        for expected_reuse in (False, True):
            result = asyncio.run(
                download_and_import(
                    db,
                    transport,
                    local_identity,
                    target,
                    contribution_id="contribution-root",
                    revision_id=bundle["revisionId"],
                    bounds=BOUNDS,
                    response_bytes=131072,
                    confirm_uncertain=True,
                    credential_ref=only_identity_ref(db),
                )
            )
            assert result.reused is expected_reuse
        keys[0]["status"] = "revoked"
        with pytest.raises(CommunityFailure, match="invalid_artifact"):
            asyncio.run(
                download_and_import(
                    db,
                    transport,
                    local_identity,
                    target,
                    contribution_id="contribution-root",
                    revision_id=bundle["revisionId"],
                    bounds=BOUNDS,
                    response_bytes=131072,
                    confirm_uncertain=True,
                    credential_ref=only_identity_ref(db),
                )
            )
        assert db.query(CommunityImportRow).count() == 1
    assert [request.url.path for request in calls] == [
        path
        for _ in range(3)
        for path in ("/v1/receipt-keys", f"/v1/contributions/{bundle['revisionId']}/artifact")
    ]
    assert len({request.headers["signature-input"] for request in calls}) == len(calls)


@pytest.mark.parametrize(
    "fault",
    ["wrong_service", "unknown_field", "offline", "wrong_digest", "changed_length", "encoding"],
)
def test_key_discovery_failure_never_fetches_artifact_or_writes(import_database, tmp_path, fault):
    bundle, keys, _ = download_input()
    calls = []

    def handle(request):
        calls.append(request.url.path)
        assert request.url.path == "/v1/receipt-keys"
        history = {"version": 1, "service": SERVICE, "keys": keys}
        if fault == "wrong_service":
            history["service"] = "https://attacker.example"
        elif fault == "unknown_field":
            history["privateKey"] = "untrusted"
        elif fault == "offline":
            return httpx.Response(503, json={"error": {"code": "artifact_unavailable"}})
        output = response(history)
        if fault == "wrong_digest":
            output.headers["content-digest"] = "sha-256=:wrong:"
        elif fault == "changed_length":
            output.headers["content-length"] = "1"
        elif fault == "encoding":
            output.headers["content-encoding"] = "identity"
        return output

    async def delay(seconds):
        pass

    transport = CommunityTransport(
        ServiceEndpoint(SERVICE, "community.example"),
        adapter=httpx.MockTransport(handle),
        delay=delay,
    )
    with Session(import_database) as db:
        with pytest.raises(CommunityFailure):
            asyncio.run(
                download_and_import(
                    db,
                    transport,
                    identity(tmp_path),
                    local_target(verified_input(), tmp_path),
                    contribution_id="contribution-root",
                    revision_id=bundle["revisionId"],
                    bounds=BOUNDS,
                    response_bytes=131072,
                    confirm_uncertain=True,
                    credential_ref=only_identity_ref(db),
                )
            )
        assert db.query(CommunityImportRow).count() == 0
    assert set(calls) == {"/v1/receipt-keys"}


def test_bounded_explicit_larger_response_keeps_enrollment_default_small(tmp_path):
    output = {"data": "x" * 20000}
    transport = CommunityTransport(
        ServiceEndpoint(SERVICE, "community.example"),
        adapter=httpx.MockTransport(lambda _: response(output)),
    )
    local_identity = identity(tmp_path)
    with pytest.raises(CommunityFailure, match="invalid_response"):
        asyncio.run(transport.request("GET", "/v1/identity", identity=local_identity))
    assert (
        asyncio.run(
            transport.request(
                "GET",
                "/v1/test",
                identity=local_identity,
                response_bytes=21000,
                verify_response_digest=True,
            )
        )
        == output
    )
    with pytest.raises(CommunityFailure, match="invalid_response"):
        asyncio.run(
            transport.request(
                "GET",
                "/v1/test",
                identity=local_identity,
                response_bytes=19000,
                verify_response_digest=True,
            )
        )


def test_restore_reuses_original_import_association_and_operation(import_database, tmp_path):
    import sqlite3

    from sqlalchemy import create_engine

    from scanner.db import CommunityOperationRow
    from scanner.persistence.repository.community_imports import import_verified_download

    downloaded = verified_input()
    target = local_target(downloaded, tmp_path)
    with Session(import_database) as db:
        original = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        intent = db.query(CommunityOperationRow).one().operation_id
    restored_path = tmp_path / "restored.db"
    with (
        sqlite3.connect(import_database.url.database) as source,
        sqlite3.connect(restored_path) as restored,
    ):
        source.backup(restored)
    engine = create_engine(f"sqlite:///{restored_path}")
    try:
        with Session(engine) as db:
            reused = import_verified_download(
                db,
                downloaded,
                target,
                bounds=BOUNDS,
                confirm_uncertain=True,
                credential_ref=only_identity_ref(db),
            )
            assert reused.reused and reused.scan.id == original.scan.id
            assert db.query(CommunityOperationRow).one().operation_id == intent
            assert db.query(CommunityImportRow).count() == 1
    finally:
        engine.dispose()


def test_concurrent_retries_create_one_immutable_scan_and_receipt(import_database, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from scanner.db import CommunityImportTargetRow, CommunityOperationRow
    from scanner.persistence.repository.community_imports import import_verified_download

    downloaded = verified_input("partial")
    target = local_target(downloaded, tmp_path)

    def perform_import():
        with Session(import_database) as db:
            return import_verified_download(
                db,
                downloaded,
                target,
                bounds=BOUNDS,
                confirm_uncertain=True,
                credential_ref=only_identity_ref(db),
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda attempt: perform_import(), range(4)))
    assert len({result.scan.id for result in results}) == 1
    assert sum(not result.reused for result in results) == 1
    with Session(import_database) as db:
        assert db.query(CommunityImportRow).count() == 1
        assert db.query(CommunityImportTargetRow).count() == 1
        assert db.query(CommunityOperationRow).count() == 1
    assert results[0].scan.community_provenance.coverage == [
        item.model_dump() for item in downloaded.verified.artifact.artifact.coverage
    ]


@pytest.mark.parametrize("role", ["audio", "video", "both"])
def test_canonical_both_cues_can_be_reviewed_without_reclassifying_source(
    import_database, tmp_path, role
):
    from scanner import repository
    from scanner.models import ReviewCreate, ReviewFilterInput, ReviewSave, ReviewScanMembership
    from scanner.persistence.repository.community_imports import import_verified_download

    downloaded = verified_input("manual")
    artifact = downloaded.verified.artifact.artifact
    with Session(import_database) as db:
        result = import_verified_download(
            db,
            downloaded,
            local_target(downloaded, tmp_path),
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        review = repository.create_saved_review(
            db,
            media_item_id="media-legacy",
            media_source_id=None,
            duration_seconds=artifact.media.duration_seconds,
            data=ReviewCreate(
                name="Role specific",
                memberships=[ReviewScanMembership(scan_id=result.scan.id, role=role)],
            ),
        )
        assert len(review.filters) == sum(
            role == "both" or item.channel in {role, "both"} for item in artifact.findings
        )
        saved = repository.save_saved_review(
            db,
            review.id,
            ReviewSave(
                revision=review.revision,
                name=review.name,
                memberships=review.memberships,
                mcf_document=review.mcf_document,
                synchronization=review.synchronization,
                filters=[ReviewFilterInput(**item.model_dump()) for item in review.filters],
            ),
        )
        assert saved.filters == review.filters
