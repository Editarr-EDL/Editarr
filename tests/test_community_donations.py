"""Stable source snapshots and exact-byte, privacy-preserving donation preparation."""

import gzip
import json
from dataclasses import replace
from datetime import datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import (
    ArchivedSource,
    Base,
    CommunityImportRow,
    MediaItemRow,
    ReviewFilterRow,
    ReviewScanMembershipRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanRow,
)
from scanner.formats.community.codec import ArtifactError, ResourceBounds
from scanner.integrations.community.donation_projection import DonationOptions
from scanner.integrations.community.donation_sources import SourceRef, capture_source
from scanner.integrations.community.donations import approve_donation, prepare_donation
from scanner.modeling.media_identity import MediaIdentity, ProviderIdentifier
from scanner.persistence.archive import archived_reads
from tests.test_stream_fingerprints import generated_streams as generated_streams

BOUNDS = ResourceBounds(65536, 524288, 100, 4096, 24)


@pytest.fixture
def source_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(
            MediaItemRow(
                id="media",
                server_id="http://jellyfin.test",
                jellyfin_id="private-id",
                item_type="Movie",
                name="PRIVATE MEDIA NAME",
                path="/private/media.mp4",
            )
        )
        db.add(
            ScanRow(
                id="scan",
                media_item_id="media",
                scan_number=1,
                source_path="/private/media.mp4",
                resolved_local_path="/private/media.mp4",
                status="complete",
                duration_seconds=10,
                scan_audio=True,
                scan_video=False,
                origin="generated",
                created_at=datetime(2026, 10, 3),
            )
        )
        db.add(
            ScanFindingRow(
                scan_id="scan",
                source="audio",
                category="language",
                subcategory="swearing",
                start_seconds=1,
                end_seconds=2,
                confidence=None,
                selected=True,
                edit_action="mute",
                details="PRIVATE FULL TRANSCRIPT",
            )
        )
        db.commit()
    yield engine
    engine.dispose()


@pytest.mark.parametrize("archived", ["scan", "media"])
def test_source_capture_never_reads_archived_work(source_database, archived):
    with Session(source_database) as db, archived_reads(db):
        archived_item = MediaItemRow(
            id="archived",
            server_id="radarr:http://radarr.test",
            jellyfin_id="12",
            item_type="Movie",
            name="Archived",
            archived_source=ArchivedSource.RADARR,
        )
        db.add(archived_item)
        db.flush()
        if archived == "scan":
            db.get(ScanRow, "scan").media_item_id = "archived"
        else:
            db.get(MediaItemRow, "media").archived_source = ArchivedSource.RADARR
        db.commit()
    with pytest.raises(ArtifactError, match="Donation source is unavailable"):
        capture_source(source_database, SourceRef("scan", "scan"), bounds=BOUNDS, source_limit=4)


def test_source_capture_is_immutable_and_changes_with_findings(source_database):
    reference = SourceRef("scan", "scan")
    captured = capture_source(source_database, reference, bounds=BOUNDS, source_limit=4)
    data = captured.data()
    data["root"]["name"] = "changed copy"
    assert captured.data()["root"]["name"] is None
    with Session(source_database) as db:
        row = db.query(ScanFindingRow).one()
        row.end_seconds = 3
        db.commit()
    assert (
        capture_source(source_database, reference, bounds=BOUNDS, source_limit=4).guard
        != captured.guard
    )
    assert json.loads(captured.private_bytes)["findings"][0]["end_seconds"] == 2


def test_source_capture_refuses_active_and_missing_sources(source_database):
    with pytest.raises(ArtifactError, match="unavailable"):
        capture_source(source_database, SourceRef("scan", "missing"), bounds=BOUNDS, source_limit=4)
    with Session(source_database) as db:
        db.get(ScanRow, "scan").status = "running"
        db.commit()
    with pytest.raises(ArtifactError, match="settled"):
        capture_source(source_database, SourceRef("scan", "scan"), bounds=BOUNDS, source_limit=4)


def options(**changes):
    return replace(
        DonationOptions(
            "submission-1",
            "Public donation",
            "contributor-1",
            "key-1",
            "2026-10-03T00:00:00Z",
            MediaIdentity("movie", (ProviderIdentifier("tmdb", "movie", "123", "user_confirmed"),)),
            10,
            sharing_terms="test-only-not-for-publication",
        ),
        **changes,
    )


def prepare(engine, **changes):
    return prepare_donation(
        engine, SourceRef("scan", "scan"), options(**changes), bounds=BOUNDS, source_limit=4
    )


def test_exact_preview_signature_and_private_canaries(source_database):
    prepared = prepare(source_database, notes=(("1", "Public event note"),))
    preview = json.loads(prepared.preview_bytes)
    assert json.loads(gzip.decompress(prepared.artifact_bytes)) == preview["artifact"]
    assert preview["manifest"]["summary"]["total"] == 1
    assert preview["artifact"]["findings"][0]["note"] == "Public event note"
    assert preview["artifact"]["audio_association"] == "unknown"
    assert preview["artifact"]["provenance"]["release_version"] is None
    for canary in (
        b"PRIVATE FULL TRANSCRIPT",
        b"PRIVATE MEDIA NAME",
        b"private-id",
        b"/private/media.mp4",
        b"requested_audio_selection",
        b"details",
    ):
        assert canary not in prepared.preview_bytes
        assert canary not in gzip.decompress(prepared.artifact_bytes)
    key = Ed25519PrivateKey.generate()
    signed = approve_donation(
        source_database, prepared, consent_token=prepared.consent_token, private_key=key
    )
    assert (
        signed.artifact_bytes == prepared.artifact_bytes
        and signed.manifest_bytes == prepared.manifest_bytes
    )


def test_stale_or_changed_preview_cannot_be_signed(source_database):
    prepared = prepare(source_database)
    key = Ed25519PrivateKey.generate()
    with pytest.raises(ArtifactError, match="consent"):
        approve_donation(source_database, prepared, consent_token="wrong", private_key=key)
    tampered = replace(prepared, preview_bytes=prepared.preview_bytes + b" ")
    with pytest.raises(ArtifactError, match="consent"):
        approve_donation(
            source_database, tampered, consent_token=prepared.consent_token, private_key=key
        )
    with Session(source_database) as db:
        db.get(ScanRow, "scan").name = "A source edit"
        db.commit()
    with pytest.raises(ArtifactError, match="Source changed"):
        approve_donation(
            source_database, prepared, consent_token=prepared.consent_token, private_key=key
        )


def test_explicit_terms_are_required(source_database):
    prepared = prepare(source_database, sharing_terms=None)
    with pytest.raises(ArtifactError, match="sharing terms"):
        approve_donation(
            source_database,
            prepared,
            consent_token=prepared.consent_token,
            private_key=Ed25519PrivateKey.generate(),
        )


def audio_selection(index=1, title="English actually analyzed", language="en"):
    return json.dumps(
        {
            "file_version": "a" * 64,
            "track": {
                "stream_index": index,
                "language": language,
                "title": title,
                "codec": "aac",
                "channels": 2,
                "disposition": 0,
                "start_seconds": 0.0,
                "duration_seconds": 10.0,
                "time_base": "1/48000",
            },
        }
    )


def seed_mixed_review(engine):
    with Session(engine) as db:
        first = db.get(ScanRow, "scan")
        first.analyzed_audio_selection_json = audio_selection()
        first.requested_audio_selection_json = audio_selection(
            7, "PRIVATE REQUESTED INVENTORY", "de"
        )
        db.add(
            ScanRow(
                id="alternate",
                media_item_id="media",
                scan_number=2,
                source_path="/private/media.mp4",
                resolved_local_path="/private/media.mp4",
                status="complete",
                duration_seconds=10,
                scan_audio=True,
                scan_video=True,
                origin="generated",
                analyzed_audio_selection_json=audio_selection(
                    2, "PRIVATE ALTERNATE INVENTORY", "fr"
                ),
                created_at=datetime(2026, 10, 3),
            )
        )
        db.add(
            ScanFindingRow(
                id=2,
                scan_id="alternate",
                source="audio",
                category="language",
                subcategory="swearing",
                start_seconds=1,
                end_seconds=2,
                confidence=None,
                edit_action="mute",
            )
        )
        db.add(
            SavedReviewRow(
                id="review",
                media_item_id="media",
                media_source_id="",
                name="Private review name",
                revision=3,
            )
        )
        db.add_all(
            [
                ReviewScanMembershipRow(
                    review_id="review", scan_id="scan", role="audio", position=0
                ),
                ReviewScanMembershipRow(
                    review_id="review", scan_id="alternate", role="both", position=1
                ),
            ]
        )
        db.add(
            ReviewSynchronizationRow(
                review_id="review", start_seconds=0, end_seconds=20, end_mode="file_end"
            )
        )
        db.add(
            ScanMcfDocumentRow(
                scan_id="scan",
                source_version="1.1.0",
                material_start_seconds=0,
                material_end_seconds=10,
            )
        )
        for identifier, scan_id, finding_id, source, channel, position in (
            ("f1", "scan", 1, "audio", "audio", 0),
            ("f2", "alternate", 2, "audio", "audio", 1),
            ("f3", "alternate", None, "visual", "video", 2),
        ):
            db.add(
                ReviewFilterRow(
                    id=identifier,
                    review_id="review",
                    source_scan_id=scan_id,
                    source_finding_id=finding_id,
                    position=position,
                    source=source,
                    mcf_channel=channel,
                    category="language" if source == "audio" else None,
                    subcategory="swearing" if source == "audio" else None,
                    start_seconds=4,
                    end_seconds=6,
                    confidence=None,
                    edit_action="mute" if source == "audio" else "skip",
                    selected=True,
                    details="PRIVATE REVIEW DEBUG",
                )
            )
        db.commit()


@pytest.mark.parametrize("origin", ["manual", "imported"])
def test_non_ai_evidence_and_remote_source_attribution(source_database, origin):
    with Session(source_database) as db:
        scan = db.get(ScanRow, "scan")
        scan.origin = origin
        scan.release_version = "CURRENT VERSION MUST NOT BECOME AI PROVENANCE"
        scan.analyzed_audio_selection_json = audio_selection()
        if origin == "imported":
            db.add(
                CommunityImportRow(
                    id="import",
                    scan_id="scan",
                    service_url="https://private-provider.example/?token=secret",
                    contribution_id="original",
                    revision_id="original-revision",
                    digest="a" * 64,
                    author_id="original-author",
                    artifact_type="scan",
                )
            )
        db.commit()
    prepared = prepare(source_database)
    artifact = json.loads(prepared.preview_bytes)["artifact"]
    assert artifact["kind"] == "manual" and artifact["audio_association"] == "unknown"
    assert (
        artifact["findings"][0]["source"] == "manual"
        and artifact["findings"][0]["confidence"] is None
    )
    assert (
        artifact["provenance"]["stages"] == [] and artifact["provenance"]["release_version"] is None
    )
    assert (
        b"CURRENT VERSION" not in prepared.preview_bytes
        and b"private-provider" not in prepared.preview_bytes
    )
    if origin == "imported":
        assert artifact["source_references"] == [
            {"submission_id": "original-revision", "digest": "a" * 64}
        ]
        assert artifact["sources"][0]["author_id"] == "original-author"


def test_zero_findings_and_redacted_public_text(source_database):
    with Session(source_database) as db:
        db.get(ScanRow, "scan").analyzed_audio_selection_json = audio_selection(
            title="/private/secret.mp4 token=private"
        )
        db.query(ScanFindingRow).delete()
        db.commit()
    prepared = prepare(source_database)
    preview = json.loads(prepared.preview_bytes)
    assert preview["manifest"]["summary"]["total"] == 0
    assert preview["artifact"]["analyzed_audio"]["description"] is None
    assert "redacted:audio_description" in preview["warnings"]
    assert b"/private/secret" not in prepared.preview_bytes


def test_actual_stream_preparation_and_file_change_guard(source_database, generated_streams):
    from scanner.formats.community.stream_fingerprints import PCM_AUDIO_ALGORITHM
    from scanner.integrations.community.pcm_fingerprints import fingerprint_pcm_audio
    from scanner.integrations.community.stream_fingerprints import fingerprint_video
    from scanner.modeling.model_audio_tracks import AudioSelection
    from scanner.pipeline.audio_tracks import probe_audio_tracks
    from scanner.repository import compute_video_md5

    path, _ = generated_streams
    inventory = probe_audio_tracks(path)
    selected = AudioSelection.from_selection(inventory.select(2))
    with Session(source_database) as db:
        scan = db.get(ScanRow, "scan")
        scan.source_path = scan.resolved_local_path = str(path)
        scan.duration_seconds = 3
        scan.scan_video = True
        scan.video_hash = compute_video_md5(str(path))
        scan.analyzed_audio_selection_json = selected.model_dump_json()
        scan.requested_audio_selection_json = AudioSelection.from_selection(
            probe_audio_tracks(path).select(1)
        ).model_dump_json()
        db.commit()
    prepared = prepare(source_database, duration_seconds=3)
    payload = json.loads(prepared.preview_bytes)["artifact"]
    actual = payload["matching"]["audio_stream"]
    recipient = fingerprint_pcm_audio(path, inventory.select(2), provenance="selected")
    requested = fingerprint_pcm_audio(path, inventory.select(1), provenance="selected")
    assert actual["algorithm"] == recipient.algorithm == PCM_AUDIO_ALGORITHM
    assert actual["digest"] == recipient.digest != requested.digest
    assert actual["playback_start"] == recipient.playback_start == "0"
    assert actual["duration"] == recipient.duration
    assert actual["provenance"] == "analyzed"
    assert payload["matching"]["video_stream"]["digest"] == fingerprint_video(path).digest
    assert payload["matching"]["fingerprints"]
    assert payload["matching"]["media"]["release"]["duration"] == {
        "seconds": 3.0,
        "source": "file_probe",
        "precision_seconds": None,
    }
    assert b"stream_index" not in prepared.preview_bytes
    with path.open("ab") as output:
        output.write(b"changed-generation")
    with pytest.raises(ArtifactError, match="Media changed"):
        approve_donation(
            source_database,
            prepared,
            consent_token=prepared.consent_token,
            private_key=Ed25519PrivateKey.generate(),
        )


def test_bounds_and_current_requested_selection_do_not_relabel_actual(source_database):
    with Session(source_database) as db:
        scan = db.get(ScanRow, "scan")
        scan.analyzed_audio_selection_json = audio_selection()
        scan.requested_audio_selection_json = audio_selection(
            2, "Other currently requested track", "fr"
        )
        db.commit()
    before = prepare(source_database)
    with Session(source_database) as db:
        db.get(ScanRow, "scan").requested_audio_selection_json = audio_selection(
            3, "Another requested track", "de"
        )
        db.commit()
    after = prepare(source_database)
    assert before.artifact_bytes == after.artifact_bytes
    assert json.loads(after.preview_bytes)["artifact"]["analyzed_audio"]["language"] == "en"
    with pytest.raises(ArtifactError):
        prepare_donation(
            source_database,
            SourceRef("scan", "scan"),
            options(),
            bounds=ResourceBounds(64, 64, 1, 16, 4),
            source_limit=1,
        )
    seed_mixed_review(source_database)
    with pytest.raises(ArtifactError, match="bound"):
        prepare_donation(
            source_database,
            SourceRef("review", "review"),
            options(duration_seconds=20, audio_source_scan_id="scan"),
            bounds=BOUNDS,
            source_limit=1,
        )
