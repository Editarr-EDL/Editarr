"""Authored review exclusions, effective coverage, and public work associations."""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.orm import Session

from scanner.db import MediaItemRow, ReviewFilterRow, SavedReviewRow, ScanRow, ScanStageCoverageRow
from scanner.formats.community.codec import ArtifactError
from scanner.integrations.community.donation_sources import SourceRef, capture_source
from scanner.integrations.community.donations import approve_donation, prepare_donation
from scanner.modeling.community_work_refs import identity_from_reference
from scanner.persistence.repository.media_identity_store import bind_review_version, observe_version
from scanner.runtime.media_fingerprints import content_digest
from tests.test_community_donations import (
    BOUNDS,
    audio_selection,
    options,
    prepare,
    seed_mixed_review,
)
from tests.test_community_donations import source_database as source_database


def test_pinned_review_cannot_claim_replacement_file_for_community(source_database, tmp_path):
    seed_mixed_review(source_database)
    path = tmp_path / "original.mkv"
    path.write_bytes(b"original physical generation")
    with Session(source_database) as db:
        version_id = observe_version(
            db,
            media=db.get(MediaItemRow, "media"),
            media_source_id=None,
            source_path=str(path),
            resolved_path=str(path),
            content_sha256=content_digest(path),
        )
        bind_review_version(db, "review", version_id)
        db.commit()
    path.write_bytes(b"replacement physical generation")
    with pytest.raises(ArtifactError, match="original review version is unavailable"):
        prepare_donation(
            source_database,
            SourceRef("review", "review"),
            options(duration_seconds=20, audio_source_scan_id="scan"),
            bounds=BOUNDS,
            source_limit=4,
        )


def test_mixed_review_exclusions_retiming_and_saved_source_unchanged(source_database):
    seed_mixed_review(source_database)
    reference = SourceRef("review", "review")
    before = capture_source(source_database, reference, bounds=BOUNDS, source_limit=4)
    with pytest.raises(ArtifactError, match="Choose one audio"):
        prepare_donation(
            source_database, reference, options(duration_seconds=20), bounds=BOUNDS, source_limit=4
        )
    prepared = prepare_donation(
        source_database,
        reference,
        options(duration_seconds=20, audio_source_scan_id="scan"),
        bounds=BOUNDS,
        source_limit=4,
    )
    preview = json.loads(prepared.preview_bytes)
    artifact = preview["artifact"]
    assert len(artifact["findings"]) == 2
    assert [item["channel"] for item in artifact["findings"]] == ["audio", "video"]
    assert [item["source"] for item in artifact["findings"]] == ["derived", "manual"]
    assert [(item["start_seconds"], item["end_seconds"]) for item in artifact["findings"]] == [
        (4, 6),
        (4, 6),
    ]
    assert artifact["source_revision"] == 3 and artifact["media"]["timeline"] == "derived_target"
    assert artifact["analyzed_audio"]["description"] == "English actually analyzed"
    assert any(
        item["mapping"] == "affine" and item["applied"] is True for item in artifact["retiming"]
    )
    assert preview["exclusions"] == [
        {"event_id": "event-2", "reason": "other_audio_track", "channel": "audio"}
    ]
    for private in (
        b"PRIVATE ALTERNATE INVENTORY",
        b"PRIVATE REQUESTED INVENTORY",
        b"PRIVATE REVIEW DEBUG",
        b"stream_index",
        b"file_version",
    ):
        assert private not in prepared.preview_bytes
    assert (
        capture_source(source_database, reference, bounds=BOUNDS, source_limit=4).guard
        == before.guard
    )
    with Session(source_database) as db:
        assert db.query(ReviewFilterRow).count() == 3
        db.get(SavedReviewRow, "review").revision += 1
        db.commit()
    with pytest.raises(ArtifactError, match="Source changed"):
        approve_donation(
            source_database,
            prepared,
            consent_token=prepared.consent_token,
            private_key=Ed25519PrivateKey.generate(),
        )


def test_effective_partial_coverage_and_private_settings(source_database):
    with Session(source_database) as db:
        db.get(ScanRow, "scan").status = "failed"
        db.add(
            ScanStageCoverageRow(
                scan_id="scan",
                stage="audio_analysis",
                execution_id="PRIVATE EXECUTION TOKEN",
                status="partial",
                source="generated",
                release_version="old-release",
                scanner_behavior_version="old-scanner",
                model_id="actual-model",
                model_revision="actual-revision",
                effective_settings_json=json.dumps(
                    {
                        "audio_model": "actual-model",
                        "api_key": "PRIVATE SECRET",
                        "audio_selection": json.loads(
                            audio_selection(2, "PRIVATE ALTERNATE INVENTORY")
                        ),
                    }
                ),
                intervals_json=json.dumps([{"start_seconds": 0, "end_seconds": 5}]),
            )
        )
        db.commit()
    prepared = prepare(source_database)
    artifact = json.loads(prepared.preview_bytes)["artifact"]
    assert artifact["coverage"][0]["state"] == "partial"
    stage = artifact["provenance"]["stages"][0]
    assert stage["release_version"] == "old-release" and stage["model_id"] == "actual-model"
    assert stage["settings"] == {"audio_model": "actual-model"}
    for private in (
        b"PRIVATE SECRET",
        b"PRIVATE EXECUTION TOKEN",
        b"PRIVATE ALTERNATE INVENTORY",
        b"audio_selection",
    ):
        assert private not in prepared.preview_bytes


@pytest.mark.parametrize(
    "provider,entity,reference",
    [
        ("tmdb", "movie", "123"),
        ("tmdb", "movie", "https://www.themoviedb.org/movie/123-title"),
        ("tmdb", "episode", "https://www.themoviedb.org/tv/12/season/0/episode/2"),
        ("tvdb", "series", "https://www.thetvdb.com/dereferrer/series/7"),
        ("tvdb", "episode", "https://thetvdb.com/?tab=episode&id=7"),
    ],
)
def test_explicit_provider_ids_and_urls(provider, entity, reference):
    identity = identity_from_reference(provider, entity, reference)
    assert identity.input_requirement is None
    assert identity.identifiers or (identity.series_identifiers and identity.episode_numbers)


@pytest.mark.parametrize(
    "reference",
    [
        "https://imdb.com/title/tt123",
        "https://themoviedb.org.evil/movie/123",
        "https://user:password@themoviedb.org/movie/123",
        "https://themoviedb.org/movie/123?token=secret",
        "0",
    ],
)
def test_private_or_unsupported_references_are_rejected(reference):
    with pytest.raises(ValueError):
        identity_from_reference("tmdb", "movie", reference)


def test_unknown_audio_sources_require_a_choice_and_both_preserves_video(source_database):
    seed_mixed_review(source_database)
    with Session(source_database) as db:
        for scan in db.query(ScanRow):
            scan.analyzed_audio_selection_json = None
        db.get(ReviewFilterRow, ("review", "f3")).mcf_channel = "both"
        db.commit()
    reference = SourceRef("review", "review")
    with pytest.raises(ArtifactError, match="Choose one audio"):
        prepare_donation(
            source_database, reference, options(duration_seconds=20), bounds=BOUNDS, source_limit=4
        )
    prepared = prepare_donation(
        source_database,
        reference,
        options(duration_seconds=20, audio_source_scan_id="scan"),
        bounds=BOUNDS,
        source_limit=4,
    )
    preview = json.loads(prepared.preview_bytes)
    assert preview["artifact"]["audio_association"] == "unknown"
    assert [item["channel"] for item in preview["artifact"]["findings"]] == ["audio", "video"]
    assert [item["event_id"] for item in preview["exclusions"]] == ["event-2", "event-3"]
    with Session(source_database) as db:
        assert db.get(ReviewFilterRow, ("review", "f3")).mcf_channel == "both"


def test_parent_event_labels_do_not_export_local_finding_identifiers(source_database):
    from scanner.db import ScanFindingRow

    seed_mixed_review(source_database)
    with Session(source_database) as db:
        db.get(ScanFindingRow, 1).id = 987654
        db.get(ReviewFilterRow, ("review", "f1")).source_finding_id = 987654
        db.commit()
    prepared = prepare_donation(
        source_database,
        SourceRef("review", "review"),
        options(duration_seconds=20, audio_source_scan_id="scan"),
        bounds=BOUNDS,
        source_limit=4,
    )
    assert b"987654" not in prepared.preview_bytes
    assert (
        json.loads(prepared.preview_bytes)["artifact"]["findings"][0]["parent_event_id"]
        == "source-2-event-1"
    )
