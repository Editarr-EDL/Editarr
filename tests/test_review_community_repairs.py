"""Atomic community restoration uses signed fixtures and preserved database rows."""

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import (
    CommunityImportRow,
    CommunityOperationRow,
    ReviewFilterRow,
    ReviewPolicyEvidenceRow,
    ReviewScanMembershipRow,
    ReviewScanRepairRow,
    ScanFindingRow,
    ScanRow,
    initialize_sqlite_connection,
)
from scanner.integrations.community.transport import CommunityFailure
from scanner.models import ReviewCreate, ReviewScanMembership
from scanner.persistence.repository.community_imports import import_verified_download
from scanner.persistence.repository.review_community_repairs import (
    RepairDownload,
    restore_community_review,
)
from tests.community_identity_support import only_identity_ref
from tests.retired_protocol_support import RETIRED_REVISION_PROTOCOL
from tests.test_community_verified_import import BOUNDS, local_target, verified_input
from tests.test_community_verified_import import import_database as import_database


@pytest.fixture
def restoration(import_database, tmp_path):
    event.listen(import_database, "connect", initialize_sqlite_connection)
    downloaded = verified_input("retimed")
    target = replace(local_target(downloaded, tmp_path), selected_channels=("video",))
    Path(target.resolved_local_path).write_bytes(b"synthetic media")
    with Session(import_database) as db:
        db.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
        assert db.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        result = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            credential_ref=only_identity_ref(db),
            confirm_uncertain=True,
        )
        review = repository.create_saved_review(
            db,
            media_item_id=target.media_item_id,
            media_source_id=None,
            data=ReviewCreate(
                name="Restore",
                memberships=[
                    ReviewScanMembership(
                        scan_id=result.scan.id,
                        role="video",
                    )
                ],
            ),
        )
        imported = db.get(CommunityImportRow, result.import_id)
        identity = {
            key: getattr(imported, key)
            for key in (
                "service_url",
                "contribution_id",
                "revision_id",
                "digest",
                "artifact_type",
                "event_ids_json",
                "receipt_json",
            )
        }
        preserved = {}
        for model, column, value in (
            (ReviewScanMembershipRow, "review_id", review.id),
            (ReviewFilterRow, "review_id", review.id),
            (ReviewPolicyEvidenceRow, "review_id", review.id),
            (ScanFindingRow, "scan_id", result.scan.id),
        ):
            preserved[model.__tablename__] = [
                dict(row)
                for row in db.execute(
                    select(model.__table__).where(getattr(model, column) == value)
                ).mappings()
            ]
            db.query(model).filter(getattr(model, column) == value).delete()
        db.query(CommunityOperationRow).filter_by(scan_id=result.scan.id).update({"scan_id": None})
        db.delete(imported)
        db.flush()
        db.query(ScanRow).filter_by(id=result.scan.id).delete()
        db.add(
            ScanFindingRow(
                id=max(value["id"] for value in preserved["scan_findings"]) + 1,
                scan_id=db.query(ScanRow).first().id,
                source="visual",
                start_seconds=0,
                end_seconds=1,
                selected=False,
                edit_action="skip",
            )
        )
        record = ReviewScanRepairRow(
            review_id=review.id,
            missing_scan_id=result.scan.id,
            detected_at=datetime.now(timezone.utc),
            preserved_json=json.dumps(preserved, default=lambda value: value.isoformat()),
            community_json=json.dumps(identity),
        )
        db.add(record)
        db.commit()
        attempt = RepairDownload(
            record.id,
            record.missing_scan_id,
            record.preserved_json,
            record.community_json,
            downloaded,
            target,
        )
        yield db, review, attempt


@RETIRED_REVISION_PROTOCOL
def test_redownload_restores_rows_and_rebinds_receipt_in_one_commit(restoration):
    db, before, attempt = restoration
    commits = []
    event.listen(db, "after_commit", lambda _session: commits.append("committed"))
    result = restore_community_review(
        db,
        before.id,
        [attempt],
        bounds=BOUNDS,
        credential_ref=only_identity_ref(db),
        confirm_uncertain=True,
    )
    assert commits == ["committed"]
    assert result.repair is None
    assert result.memberships[0].scan_id != attempt.missing_scan_id
    assert [(cue.start_seconds, cue.end_seconds, cue.mcf_comment) for cue in result.filters] == [
        (cue.start_seconds, cue.end_seconds, cue.mcf_comment) for cue in before.filters
    ]
    assert result.filters
    assert {value.source_finding_id for value in result.filters}.isdisjoint(
        {value.source_finding_id for value in before.filters}
    )
    for cue in result.filters:
        finding = db.get(ScanFindingRow, cue.source_finding_id)
        assert finding.scan_id == cue.source_scan_id == result.memberships[0].scan_id
    assert db.query(CommunityOperationRow).one().scan_id == result.memberships[0].scan_id
    assert db.query(ReviewScanRepairRow).filter_by(review_id=before.id).count() == 0
    assert db.connection().exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("failure", ["digest", "map", "partial_map"])
@RETIRED_REVISION_PROTOCOL
def test_unmatched_identity_or_finding_rolls_back_the_entire_import(restoration, failure):
    db, before, attempt = restoration
    record = db.get(ReviewScanRepairRow, attempt.record_id)
    identity = json.loads(record.community_json)
    if failure == "digest":
        identity["digest"] = "0" * 64
    else:
        identity["event_ids_json"] = json.dumps({"-1": "unknown"}) if failure == "map" else "{}"
        if failure == "partial_map":
            preserved = json.loads(record.preserved_json)
            preserved["scan_findings"] = []
            record.preserved_json = json.dumps(preserved)
    record.community_json = json.dumps(identity)
    db.commit()
    attempt = replace(
        attempt, community_json=record.community_json, preserved_json=record.preserved_json
    )
    with pytest.raises(CommunityFailure, match="digest_mismatch|mapping_mismatch"):
        restore_community_review(
            db,
            before.id,
            [attempt],
            bounds=BOUNDS,
            credential_ref=only_identity_ref(db),
            confirm_uncertain=True,
        )
    assert db.query(CommunityImportRow).count() == 0
    assert db.query(ReviewFilterRow).filter_by(review_id=before.id).count() == 0
    assert db.get(ReviewScanRepairRow, attempt.record_id) is not None
    assert db.query(CommunityOperationRow).one().scan_id is None


@RETIRED_REVISION_PROTOCOL
def test_no_event_map_uses_exact_source_content_only(restoration):
    db, before, attempt = restoration
    record = db.get(ReviewScanRepairRow, attempt.record_id)
    identity = json.loads(record.community_json)
    identity["event_ids_json"] = "{}"
    record.community_json = json.dumps(identity)
    db.commit()
    result = restore_community_review(
        db,
        before.id,
        [replace(attempt, community_json=record.community_json)],
        bounds=BOUNDS,
        credential_ref=only_identity_ref(db),
        confirm_uncertain=True,
    )
    assert result.repair is None and len(result.filters) == len(before.filters)


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(None, marks=RETIRED_REVISION_PROTOCOL),
        "not_found",
        "revoked",
        "offline",
        "identity",
        "config",
        "uncertain",
        "incompatible",
        "candidate_missing",
    ],
)
def test_download_outcomes_store_only_definitive_failure(
    restoration, tmp_path, monkeypatch, failure
):
    import asyncio
    from dataclasses import asdict
    from types import SimpleNamespace

    import httpx

    from scanner.integrations.community import review_repairs as module
    from scanner.integrations.community.local_downloads import CommunityDownloads
    from scanner.integrations.community.lookup_targets import CapturedTarget
    from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
    from scanner.modeling.community_matching import POLICY_VERSION, classify
    from scanner.modeling.media_identity import MediaIdentity, ProviderIdentifier
    from scanner.modeling.model_community_lookup import LookupSummary
    from scanner.pipeline.audio_tracks import _file_state
    from scanner.security.access.capability_ids import Capability as C
    from tests.test_community_downloads import identity as signing_identity
    from tests.test_community_downloads import response
    from tests.test_community_verified_import import SERVICE, download_input

    db, review, attempt = restoration
    bundle, keys, _ = download_input("retimed")
    path = Path(attempt.target.resolved_local_path)
    captured = CapturedTarget(path, _file_state(path), attempt.target.evidence, "a" * 64, ())
    if failure == "incompatible":
        captured = replace(
            captured,
            evidence=replace(
                captured.evidence,
                media=MediaIdentity(
                    "movie", (ProviderIdentifier("tmdb", "movie", "99999999", "test"),)
                ),
            ),
        )
    monkeypatch.setattr(module, "capture_target", lambda *_args: captured)
    identity = signing_identity(tmp_path)
    if failure == "identity":
        identity = replace(identity, contributor_id=None)
    if failure == "revoked":
        bundle["historicalKey"]["status"] = "revoked"

    requests = []

    def handle(request):
        requests.append(request.url.path)
        if failure == "offline":
            raise httpx.ConnectError("synthetic offline")
        if request.url.path == "/v1/matches":
            candidate_evidence = attempt.target.evidence
            candidate = {
                "contributionId": attempt.downloaded.contribution_id,
                "revisionId": attempt.downloaded.revision_id,
                "artifactDigest": attempt.downloaded.verified.manifest.manifest.artifact_digest,
                "artifactSchema": "community-artifact-2",
                "releaseId": "release",
                "catalogCreatedAt": 1,
                "attribution": {"contributorId": "author", "displayName": "Author"},
                "summary": {
                    key: value
                    for key, value in attempt.downloaded.verified.artifact.summary.items()
                    if key in LookupSummary.model_fields
                },
                "evidence": asdict(candidate_evidence),
                "compatibility": asdict(classify(captured.evidence, candidate_evidence)),
                "statistics": {"imports": 0, "likes": 0, "dislikes": 0},
                "ranking": None,
            }
            return response(
                {
                    "version": 1,
                    "policyVersion": POLICY_VERSION,
                    "candidates": [] if failure == "candidate_missing" else [candidate],
                    "nextCursor": None,
                }
            )
        if request.url.path == "/v1/receipt-keys":
            return response({"version": 1, "service": SERVICE, "keys": keys})
        if failure == "not_found":
            return httpx.Response(404, json={"error": {"code": "artifact_unavailable"}})
        return response(bundle)

    async def delay(_seconds):
        return None

    client = SimpleNamespace(
        db=db,
        _identity=lambda: identity,
        home=SimpleNamespace(credential_ref=only_identity_ref(db)),
        transport=CommunityTransport(
            ServiceEndpoint(SERVICE, "community.example"),
            adapter=httpx.MockTransport(handle),
            delay=delay,
        ),
    )

    def run(identifier, _action, operation, *args, **options):
        assert options["repair"] is True
        assert options["extra_capabilities"] == (C.COMMUNITY_DOWNLOAD,)
        return operation(db, identifier, *args)

    def service():
        if failure == "config":
            raise CommunityFailure("downloads_not_configured")
        return CommunityDownloads(client, bounds=BOUNDS, response_bytes=1_000_000)

    outcome = asyncio.run(
        module.repair_from_community(
            SimpleNamespace(db=db, run=run),
            review.id,
            SimpleNamespace(media=SimpleNamespace(resolved_path=str(path))),
            service,
            **(
                {"confirmed": True}
                if failure in {None, "not_found", "revoked", "offline", "identity", "config"}
                else {}
            ),
        )
    )
    record = db.get(ReviewScanRepairRow, attempt.record_id)
    if failure is None:
        assert isinstance(outcome, module.Restored)
        assert record is None
    elif failure in {"not_found", "revoked", "incompatible"}:
        assert isinstance(outcome, module.Unavailable)
        assert record.community_unavailable_reason == outcome.reason
    else:
        assert isinstance(outcome, module.Transient)
        assert record.community_unavailable_reason is None
    if failure not in {"identity", "config"}:
        assert "/v1/matches" in requests
    if failure == "uncertain":
        assert outcome.reason == "explicit_review_required"
        assert db.query(CommunityImportRow).count() == 0
    if failure == "candidate_missing":
        assert outcome.reason == "candidate_unavailable"
        assert not any("/downloads" in value for value in requests)
