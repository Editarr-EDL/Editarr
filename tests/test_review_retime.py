from datetime import datetime, timezone

import pytest

from scanner import repository
from scanner.db import (
    CreditsDetectionRunRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.models import (
    CreditsCandidate,
    CreditsDetectionConfiguration,
    CreditsDetectionResult,
    CreditsEvidence,
    ReviewFilterInput,
    ReviewRetimeRequest,
    ReviewSave,
    ReviewSynchronization,
)
from scanner.pipeline.cache import compute_video_md5
from tests.review_retime_request import _request

pytest_plugins = ("tests.review_retime_support",)


def test_retime_maps_complete_draft_without_persisting(retime_review):
    db, review, _media_path = retime_review
    request = _request(review)
    before = repository.get_saved_review(db, review.id)

    result = repository.retime_saved_review_draft(db, review.id, request)

    assert result is not None
    assert result.synchronization == request.proposed_synchronization
    assert result.mcf_document.material_start_seconds == 10
    assert result.mcf_document.material_end_seconds == 90
    assert result.filters[0].start_seconds == 26
    assert result.filters[0].end_seconds == 42
    assert result.filters[0].selected is False
    assert result.filters[0].model_dump(exclude={"start_seconds", "end_seconds"}) == (
        request.filters[0].model_dump(exclude={"start_seconds", "end_seconds"})
    )
    assert result.new_manual_filters[0].start_seconds == 50
    assert result.new_manual_filters[0].end_seconds == 60
    assert repository.get_saved_review(db, review.id) == before


@pytest.mark.parametrize(
    ("current", "proposed", "expected"),
    [
        ((0, 100), (0, 100), (20, 40)),
        ((0, 80), (10, 90), (30, 50)),
        ((0, 50), (0, 100), (40, 80)),
        ((0, 100), (0, 50), (10, 20)),
    ],
)
def test_retime_operation_identity_offset_stretch_and_compression(
    retime_review, current, proposed, expected
):
    db, review, _media_path = retime_review
    result = repository.retime_saved_review_draft(
        db,
        review.id,
        _request(
            review,
            current_synchronization=ReviewSynchronization(
                start_seconds=current[0], end_seconds=current[1], end_mode="file_end"
            ),
            proposed_synchronization=ReviewSynchronization(
                start_seconds=proposed[0], end_seconds=proposed[1], end_mode="file_end"
            ),
            new_manual_filters=[],
        ),
    )

    assert result is not None
    assert (result.filters[0].start_seconds, result.filters[0].end_seconds) == expected
    assert result.filters[0].selected is False


def test_retime_repeats_from_current_unsaved_values_and_round_trips_on_save(
    retime_review,
):
    db, review, _media_path = retime_review
    first = repository.retime_saved_review_draft(db, review.id, _request(review))
    assert first is not None
    edited_filter = first.filters[0].model_copy(update={"start_seconds": 30})
    second = repository.retime_saved_review_draft(
        db,
        review.id,
        ReviewRetimeRequest(
            revision=review.revision,
            memberships=review.memberships,
            current_synchronization=first.synchronization,
            proposed_synchronization=ReviewSynchronization(
                start_seconds=0, end_seconds=100, end_mode="file_end"
            ),
            mcf_document=first.mcf_document,
            filters=[edited_filter],
            accepted_credits=first.accepted_credits,
            new_manual_filters=first.new_manual_filters,
        ),
    )
    assert second is not None
    assert second.filters[0].start_seconds == 25
    assert second.filters[0].end_seconds == 40
    assert second.new_manual_filters[0].start_seconds == 50
    assert second.new_manual_filters[0].end_seconds == 60
    assert second.mcf_document.material_start_seconds == 0
    assert second.mcf_document.material_end_seconds == 90

    saved = repository.save_saved_review(
        db,
        review.id,
        ReviewSave(
            revision=review.revision,
            name=review.name,
            memberships=review.memberships,
            filters=second.filters,
            mcf_document=second.mcf_document,
            synchronization=second.synchronization,
            new_manual_filters=second.new_manual_filters,
        ),
    )
    assert saved is not None
    assert saved.synchronization == second.synchronization
    assert (saved.edl_content_revision, saved.mcf_content_revision) == (1, 1)
    assert len(saved.filters) == 2


def test_retime_preserves_local_credits_boundary_and_snapshot(
    retime_review,
):
    db, review, media_path = retime_review
    configuration = CreditsDetectionConfiguration(
        detector_version="credits-v1",
        tail_window_seconds=100,
        frame_sample_interval_seconds=2,
    )
    candidate = CreditsCandidate(
        id="candidate-1",
        boundary_seconds=90,
        range_start_seconds=84,
        range_end_seconds=96,
        confidence=0.8,
        confidence_band="high",
        method="hybrid",
        evidence=[
            CreditsEvidence(
                kind="chapter_marker",
                timestamp_seconds=90,
                score=0.9,
                detail_code="credits_chapter",
            )
        ],
    )
    credits_scan = ScanRow(
        id="credits",
        media_item_id="media",
        scan_number=2,
        scan_mode="credits_detection",
        source_path=str(media_path),
        resolved_local_path=str(media_path),
        video_hash=compute_video_md5(str(media_path)),
        duration_seconds=100,
        status="complete",
        scan_audio=False,
        scan_video=True,
    )
    finding = ScanFindingRow(
        id=2,
        scan_id=credits_scan.id,
        category="dispensable_scenes",
        subcategory="dispensable",
        severity="medium",
        mcf_channel="video",
        mcf_comment="End Credits",
        start_seconds=90,
        end_seconds=100,
        confidence=0.8,
        label="End Credits",
        details="End Credits",
        source="visual",
        edit_action="skip",
    )
    credits_scan.credits_detection = CreditsDetectionRunRow(
        configuration_json=configuration.model_dump_json(),
        detector_version=configuration.detector_version,
        result_json=CreditsDetectionResult(
            outcome="candidate",
            candidates=[candidate],
            best_candidate_id=candidate.id,
        ).model_dump_json(),
        completed_at=datetime.now(timezone.utc),
    )
    db.add_all([credits_scan, finding])
    db.commit()
    acceptance = repository.accept_credits_detection(
        db,
        scan_id=credits_scan.id,
        accepted_boundary_seconds=92,
        media_item_id="media",
        media_source_id=None,
        resolved_local_path=str(media_path),
        review_id=review.id,
        revision=review.revision,
    )
    saved = repository.save_saved_review(
        db,
        review.id,
        ReviewSave(
            revision=review.revision,
            name=review.name,
            memberships=[*review.memberships, acceptance.membership],
            filters=[
                *[ReviewFilterInput(**value.model_dump()) for value in review.filters],
                acceptance.filter,
            ],
            mcf_document=review.mcf_document,
            synchronization=review.synchronization,
            accepted_credits=acceptance.accepted_credits,
        ),
    )
    assert saved is not None and saved.accepted_credits is not None
    accepted_at = saved.accepted_credits.accepted_at

    result = repository.retime_saved_review_draft(
        db,
        saved.id,
        ReviewRetimeRequest(
            revision=saved.revision,
            memberships=saved.memberships,
            current_synchronization=saved.synchronization,
            proposed_synchronization=ReviewSynchronization(
                start_seconds=0, end_seconds=80, end_mode="file_end"
            ),
            mcf_document=saved.mcf_document,
            filters=[ReviewFilterInput(**value.model_dump()) for value in saved.filters],
            accepted_credits=saved.accepted_credits,
            new_manual_filters=[],
        ),
    )
    assert result is not None and result.accepted_credits is not None
    assert result.accepted_credits.accepted_boundary_seconds == 92
    credits_filter = next(
        value for value in result.filters if value.source_scan_id == credits_scan.id
    )
    assert credits_filter.start_seconds == 92
    assert credits_filter.end_seconds == 100

    retimed = repository.save_saved_review(
        db,
        saved.id,
        ReviewSave(
            revision=saved.revision,
            name=saved.name,
            memberships=saved.memberships,
            filters=result.filters,
            mcf_document=result.mcf_document,
            synchronization=result.synchronization,
            accepted_credits=result.accepted_credits,
        ),
    )
    assert retimed is not None and retimed.accepted_credits is not None
    assert retimed.accepted_credits.detected_boundary_seconds == 90
    assert retimed.accepted_credits.accepted_boundary_seconds == 92
    assert retimed.accepted_credits.accepted_at == accepted_at
    db.expire_all()
    immutable_scan = db.get(ScanRow, credits_scan.id)
    assert immutable_scan is not None and immutable_scan.credits_detection is not None
    immutable_result = CreditsDetectionResult.model_validate_json(
        immutable_scan.credits_detection.result_json
    )
    assert immutable_result.candidates[0].boundary_seconds == 90
    immutable_finding = db.get(ScanFindingRow, finding.id)
    assert immutable_finding is not None
    assert immutable_finding.start_seconds == 90
    assert immutable_finding.end_seconds == 100


def test_retime_rejects_stale_provenance_runtime_overflow_and_mapped_outlier(
    retime_review,
):
    db, review, _media_path = retime_review
    with pytest.raises(repository.ReviewRevisionConflict):
        repository.retime_saved_review_draft(
            db, review.id, _request(review, revision=review.revision + 1)
        )

    forged = _request(review).filters[0].model_copy(
        update={"source_scan_id": "missing"}
    )
    with pytest.raises(repository.ReviewMembershipError, match="membership"):
        repository.retime_saved_review_draft(
            db, review.id, _request(review, filters=[forged])
        )

    with pytest.raises(repository.ReviewRetimeError) as overflow:
        repository.retime_saved_review_draft(
            db,
            review.id,
            _request(
                review,
                proposed_synchronization=ReviewSynchronization(
                    start_seconds=0, end_seconds=101, end_mode="file_end"
                ),
            ),
        )
    assert overflow.value.code == "invalid_synchronization_bounds"
    assert overflow.value.document_fields == ["proposed_synchronization.end_seconds"]

    with pytest.raises(repository.ReviewRetimeError) as stale_basis:
        repository.retime_saved_review_draft(
            db,
            review.id,
            _request(
                review,
                current_synchronization=ReviewSynchronization(
                    start_seconds=0, end_seconds=101, end_mode="file_end"
                ),
            ),
        )
    assert stale_basis.value.document_fields == [
        "current_synchronization.end_seconds"
    ]

    outlier = _request(review).filters[0].model_copy(
        update={"start_seconds": 0, "end_seconds": 20}
    )
    with pytest.raises(repository.ReviewRetimeError) as mapped:
        repository.retime_saved_review_draft(
            db,
            review.id,
            _request(
                review,
                current_synchronization=ReviewSynchronization(
                    start_seconds=10, end_seconds=100, end_mode="file_end"
                ),
                proposed_synchronization=ReviewSynchronization(
                    start_seconds=0, end_seconds=100, end_mode="file_end"
                ),
                mcf_document=review.mcf_document.model_copy(
                    update={"material_start_seconds": 10}
                ),
                filters=[outlier],
            ),
        )
    assert mapped.value.code == "mapped_cue_out_of_bounds"
    assert mapped.value.filter_ids == [outlier.id]


def test_retime_rejects_unavailable_media_without_mutation(retime_review):
    db, review, media_path = retime_review
    media_path.unlink()
    with pytest.raises(repository.ReviewMediaUnavailable):
        repository.retime_saved_review_draft(db, review.id, _request(review))
    assert repository.get_saved_review(db, review.id).revision == review.revision
