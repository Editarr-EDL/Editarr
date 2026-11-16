"""Verified import tests use frozen artifact bytes, synthetic receipts and temporary SQLite."""

import base64
import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityImportRow,
    CommunityImportTargetRow,
    CommunityOperationRow,
    FilterSelectionRuleRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.formats.community.artifact_v2 import validate_v2
from scanner.formats.community.codec import ResourceBounds
from scanner.integrations.community.downloads import verify_download_bundle
from scanner.integrations.community.transport import CommunityFailure
from scanner.persistence.migrations.adoption import upgrade_existing_database
from scanner.persistence.repository.community_import_projection import LocalImportTarget
from scanner.persistence.repository.community_imports import import_verified_download
from scanner.persistence.repository.repository_community import set_connection
from scanner.security.community_download_receipts import (
    ReceiptFields,
    parse_receipt_keys,
    receipt_identity,
    receipt_signing_bytes,
)
from tests.community_identity_support import only_identity_ref
from tests.community_schema_fixture import create_unversioned_fixture

BOUNDS = ResourceBounds(65536, 131072, 100, 2048, 30)
VECTORS = json.loads(
    (Path(__file__).parent / "fixtures/community/donation-artifacts-v2.json").read_text()
)
RECEIPT_VECTOR = json.loads(
    (Path(__file__).parent / "fixtures/community/download-receipt-v1.json").read_text()
)
SERVICE = "https://community.example"


def download_input(name="ai"):
    fixture = VECTORS["cases"][name]
    original = base64.b64decode(fixture["artifact"])
    manifest = base64.b64decode(fixture["manifest"])
    envelope = base64.b64decode(fixture["envelope"])
    metadata = json.loads(manifest)
    signer = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(RECEIPT_VECTOR["syntheticSeedHex"]))
    receipt = ReceiptFields.model_validate(
        {
            "schemaVersion": "community-download-receipt-1",
            "service": SERVICE,
            "serviceKeyId": RECEIPT_VECTOR["envelope"]["receipt"]["serviceKeyId"],
            "contributorId": "reader",
            "revisionId": metadata["submission_id"],
            "artifactDigest": metadata["artifact_digest"],
            "issuedAt": 1700000030,
            "receiptId": receipt_identity(
                SERVICE, "reader", metadata["submission_id"], metadata["artifact_digest"]
            ),
        }
    )
    value = {
        "version": 1,
        "revisionId": metadata["submission_id"],
        "artifactSchema": metadata["artifact_schema_version"],
        "artifactFormat": "gzip-json",
        "artifactDigest": metadata["artifact_digest"],
        "manifestDigest": hashlib.sha256(manifest).hexdigest(),
        "envelopeDigest": hashlib.sha256(envelope).hexdigest(),
        **fixture,
        "historicalKey": {
            "contributorId": metadata["contributor_id"],
            "keyId": metadata["key_id"],
            "publicKey": VECTORS["public_key"],
            "status": "retired",
        },
        "receipt": {
            "receipt": receipt.model_dump(by_alias=True),
            "signature": base64.b64encode(signer.sign(receipt_signing_bytes(receipt))).decode(),
        },
    }
    keys = [
        {
            "keyId": receipt.service_key_id,
            "publicKey": RECEIPT_VECTOR["publicKey"],
            "status": "active",
        }
    ]
    return value, keys, (original, manifest, envelope)


def verified_input(name="ai"):
    value, keys, _ = download_input(name)
    return verify_download_bundle(
        value,
        service=SERVICE,
        contribution_id="contribution-root",
        revision_id=value["revisionId"],
        contributor_id="reader",
        trusted_keys=parse_receipt_keys(keys),
        bounds=BOUNDS,
    )


@pytest.mark.parametrize("name", list(VECTORS["cases"]))
def test_download_verifies_all_frozen_v2_source_and_retiming_variants(name):
    downloaded = verified_input(name)
    _, _, originals = download_input(name)
    assert (
        downloaded.verified.signed.artifact_bytes,
        downloaded.verified.signed.manifest_bytes,
        downloaded.verified.signed.envelope_bytes,
    ) == originals


@pytest.mark.parametrize("change", ["digest", "schema", "path", "author", "receipt", "version"])
def test_hostile_download_dtos_cannot_enter_an_import(change):
    value, keys, _ = download_input()
    value = copy.deepcopy(value)
    if change == "digest":
        value["artifactDigest"] = "b" * 64
    elif change == "schema":
        value["artifactSchema"] = "community-artifact-1"
    elif change == "path":
        value["local_path"] = "/tmp/remote"
    elif change == "author":
        value["historicalKey"]["contributorId"] = "other"
    elif change == "receipt":
        value["receipt"]["receipt"]["contributorId"] = "other"
    else:
        value["version"] = True
    with pytest.raises(CommunityFailure):
        verify_download_bundle(
            value,
            service=SERVICE,
            contribution_id="contribution-root",
            revision_id=value["revisionId"],
            contributor_id="reader",
            trusted_keys=parse_receipt_keys(keys),
            bounds=BOUNDS,
        )


@pytest.fixture
def import_database(tmp_path):
    path = tmp_path / "scanner.db"
    create_unversioned_fixture(path).dispose()
    upgrade_existing_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with Session(engine) as db:
        db.query(FilterSelectionRuleRow).one().id = "00000000-0000-0000-0000-000000000001"
        set_connection(
            db,
            credential_ref=only_identity_ref(db),
            service_url=SERVICE,
            contributor_id="reader",
            installation_id="installation-reader",
            key_id="sha256:reader",
        )
        db.commit()
    yield engine
    engine.dispose()


def local_target(downloaded, tmp_path):
    matching = validate_v2(downloaded.verified.artifact.artifact, source_limit=BOUNDS.events)
    return LocalImportTarget("media-legacy", None, str(tmp_path / "local-only.mp4"), matching)


@pytest.mark.parametrize("name", list(VECTORS["cases"]))
def test_atomic_import_preserves_bytes_source_timeline_attribution_and_reuses(
    import_database, tmp_path, name
):
    downloaded = verified_input(name)
    target = local_target(downloaded, tmp_path)
    with Session(import_database) as db:
        first = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        assert first.receipt_state == "pending"
        assert first.reused is False
        scan_id = first.scan.id
        stored = db.query(CommunityImportRow).filter_by(scan_id=scan_id).one()
        assert (
            stored.artifact_bytes,
            stored.manifest_bytes,
            stored.signature_bytes,
        ) == download_input(name)[2]
        assert (
            first.scan.community_provenance.author_id
            == downloaded.verified.historical_key.contributor_id
        )
        findings = (
            db.query(ScanFindingRow).filter_by(scan_id=scan_id).order_by(ScanFindingRow.id).all()
        )
        assert [(item.start_seconds, item.end_seconds, item.confidence) for item in findings] == [
            (item.start_seconds, item.end_seconds, item.confidence)
            for item in downloaded.verified.artifact.artifact.findings
        ]
        original_receipt = stored.receipt_json
        second = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        assert second.scan.id == scan_id
        assert second.reused is True
        assert db.query(CommunityImportRow).count() == 1
        assert db.query(CommunityImportTargetRow).count() == 1
        assert db.query(CommunityOperationRow).count() == 1
        assert db.query(CommunityOperationRow).one().receipt == original_receipt


def test_lost_response_after_import_commit_reuses_committed_scan_and_original_receipt(
    import_database, tmp_path, monkeypatch
):
    downloaded = verified_input()
    target = local_target(downloaded, tmp_path)
    with Session(import_database) as db:
        commit = db.commit

        def lost_response():
            commit()
            raise RuntimeError("lost response after commit")

        monkeypatch.setattr(db, "commit", lost_response)
        with pytest.raises(RuntimeError, match="lost response"):
            import_verified_download(
                db,
                downloaded,
                target,
                bounds=BOUNDS,
                confirm_uncertain=True,
                credential_ref=only_identity_ref(db),
            )
    value, keys, _ = download_input()
    receipt = ReceiptFields.model_validate(value["receipt"]["receipt"]).model_copy(
        update={"issued_at": 1700009999}
    )
    signer = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(RECEIPT_VECTOR["syntheticSeedHex"]))
    value["receipt"] = {
        "receipt": receipt.model_dump(by_alias=True),
        "signature": base64.b64encode(signer.sign(receipt_signing_bytes(receipt))).decode(),
    }
    refreshed = verify_download_bundle(
        value,
        service=SERVICE,
        contribution_id="contribution-root",
        revision_id=value["revisionId"],
        contributor_id="reader",
        trusted_keys=parse_receipt_keys(keys),
        bounds=BOUNDS,
    )
    with Session(import_database) as db:
        original = db.query(CommunityImportRow).one().receipt_json
        assert json.loads(original)["receipt"]["issuedAt"] == 1700000030
        result = import_verified_download(
            db,
            refreshed,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        assert result.reused and result.receipt_state == "pending"
        assert db.query(ScanRow).count() == 2
        assert db.query(CommunityOperationRow).one().receipt == original


def test_reuse_target_review_export_and_derived_sharing_preserve_immutable_source(
    import_database, tmp_path
):
    from scanner import repository
    from scanner.db import MediaItemRow, SavedReviewRow
    from scanner.integrations.community.donation_projection import DonationOptions, project_source
    from scanner.integrations.community.donation_sources import SourceRef, capture_source
    from scanner.models import ReviewCreate, ReviewFilterInput, ReviewSave, ReviewScanMembership

    downloaded = verified_input("retimed")
    target = local_target(downloaded, tmp_path)
    Path(target.resolved_local_path).write_bytes(b"synthetic media only")
    artifact = downloaded.verified.artifact.artifact
    with Session(import_database) as db:
        first = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        original_scan_id = first.scan.id
        second_path = tmp_path / "other-local-version.mp4"
        second_path.write_bytes(b"different synthetic local target")
        db.add(
            MediaItemRow(
                id="second",
                server_id="fixture",
                jellyfin_id="second",
                item_type="Movie",
                name="Second target",
                path=str(second_path),
            )
        )
        db.commit()
        second_target = replace(
            target,
            media_item_id="second",
            media_source_id="version-two",
            resolved_local_path=str(second_path),
        )
        reused = import_verified_download(
            db,
            downloaded,
            second_target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        assert reused.scan.id == original_scan_id and reused.reused
        assert db.query(CommunityImportTargetRow).count() == 2
        created = repository.create_saved_review(
            db,
            media_item_id="second",
            media_source_id="version-two",
            duration_seconds=artifact.media.duration_seconds,
            data=ReviewCreate(
                name="Separate review",
                memberships=[ReviewScanMembership(scan_id=original_scan_id, role="both")],
            ),
        )
        assert [(item.start_seconds, item.end_seconds) for item in created.filters] == [
            (item.start_seconds, item.end_seconds) for item in artifact.findings
        ]
        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=created.revision,
                name="User edited review",
                memberships=created.memberships,
                filters=[
                    ReviewFilterInput(**item.model_dump()).model_copy(
                        update={"start_seconds": item.start_seconds + 0.25}
                    )
                    for item in created.filters
                ],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )
        exported = repository.export_saved_review_edl(db, saved.id, saved.revision)
        assert exported is not None
        assert exported.exported_scans[0].community_provenance == first.scan.community_provenance
        before = db.query(CommunityImportRow).one()
        originals = (before.artifact_bytes, before.manifest_bytes, before.signature_bytes)
        repeat = import_verified_download(
            db,
            downloaded,
            second_target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        assert repeat.scan.id == original_scan_id
        assert db.get(SavedReviewRow, saved.id).name == "User edited review"
        assert db.get(SavedReviewRow, saved.id).revision == saved.revision
        assert (before.artifact_bytes, before.manifest_bytes, before.signature_bytes) == originals
    captured = capture_source(
        import_database, SourceRef("review", saved.id), bounds=BOUNDS, source_limit=4
    )
    assert all(
        key not in captured.data()["imports"][0]
        for key in (
            "artifact_bytes",
            "manifest_bytes",
            "signature_bytes",
            "receipt_json",
            "historical_key_json",
        )
    )
    derived, _, _, _ = project_source(
        captured,
        DonationOptions(
            submission_id="derived-next",
            name="Derived review",
            contributor_id="reader",
            key_id="reader-key",
            created_at="2026-10-03T00:00:00Z",
            media=target.evidence.media,
            duration_seconds=artifact.media.duration_seconds,
        ),
        bounds=BOUNDS,
    )
    assert derived["source_references"] == [
        {
            "submission_id": downloaded.revision_id,
            "digest": downloaded.verified.manifest.manifest.artifact_digest,
        }
    ]
    assert derived["sources"][0]["author_id"] == artifact.author.contributor_id
    assert [item["parent_event_id"] for item in derived["findings"]] == [
        item.event_id for item in artifact.findings
    ]


def test_import_requires_explicit_uncertain_review_and_rejects_incompatible_before_writes(
    import_database, tmp_path
):
    downloaded = verified_input("legacy")
    target = local_target(downloaded, tmp_path)
    with Session(import_database) as db:
        with pytest.raises(CommunityFailure, match="explicit_review_required"):
            import_verified_download(
                db, downloaded, target, bounds=BOUNDS, credential_ref=only_identity_ref(db)
            )
        assert db.query(CommunityImportRow).count() == 0
        identity = replace(
            target.evidence.media,
            identifiers=tuple(
                replace(item, value="999999") for item in target.evidence.media.identifiers
            ),
        )
        wrong = replace(target, evidence=replace(target.evidence, media=identity))
        with pytest.raises(CommunityFailure, match="incompatible_artifact"):
            import_verified_download(
                db,
                downloaded,
                wrong,
                bounds=BOUNDS,
                confirm_uncertain=True,
                credential_ref=only_identity_ref(db),
            )
        assert db.query(CommunityImportRow).count() == 0
        assert db.query(ScanRow).count() == 1


def test_before_commit_crash_rolls_back_all_evidence(import_database, tmp_path):
    downloaded = verified_input()

    def fail_commit(db):
        raise RuntimeError("before commit")

    with Session(import_database) as db:
        event.listen(db, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="before commit"):
            import_verified_download(
                db,
                downloaded,
                local_target(downloaded, tmp_path),
                bounds=BOUNDS,
                confirm_uncertain=True,
                credential_ref=only_identity_ref(db),
            )
        event.remove(db, "before_commit", fail_commit)
        assert db.query(ScanRow).count() == 1
        assert db.query(CommunityImportRow).count() == 0
        assert db.query(CommunityImportTargetRow).count() == 0
        assert db.query(CommunityOperationRow).count() == 0


def test_post_commit_receipt_failure_keeps_import_and_retry_recovers_original_intent(
    import_database, tmp_path, monkeypatch
):
    from scanner.persistence.repository import community_imports

    downloaded = verified_input()
    target = local_target(downloaded, tmp_path)
    with Session(import_database) as db:
        with monkeypatch.context() as patch:

            def fail_queue(*args):
                raise RuntimeError("outbox unavailable")

            patch.setattr(community_imports, "queue_import_receipt", fail_queue)
            first = import_verified_download(
                db,
                downloaded,
                target,
                bounds=BOUNDS,
                confirm_uncertain=True,
                credential_ref=only_identity_ref(db),
            )
            assert first.receipt_state == "unavailable"
        assert db.query(CommunityImportRow).count() == 1
        assert db.query(CommunityOperationRow).count() == 0
    with Session(import_database) as db:
        retry = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        assert retry.reused and retry.scan.id == first.scan.id
        assert retry.receipt_state == "pending"
        assert db.query(CommunityOperationRow).count() == 1
