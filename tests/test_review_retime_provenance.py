"""Cue retiming eligibility based on scan provenance."""

from uuid import uuid4

import pytest

from scanner import repository
from scanner.db import ScanFindingRow, ScanRow
from scanner.models import ReviewFilterInput, ReviewSave, ReviewScanMembership
from scanner.pipeline.cache import compute_video_md5
from tests.review_retime_request import _request

pytest_plugins = ("tests.review_retime_support",)


@pytest.mark.parametrize("origin", ["generated", "manual"])
def test_retime_only_changes_unverified_cues_in_mixed_review(retime_review, origin):
    db, review, media_path = retime_review
    local_scan = ScanRow(
        id="local-scan",
        media_item_id="media",
        scan_number=2,
        origin=origin,
        source_path=str(media_path),
        resolved_local_path=str(media_path),
        video_hash=compute_video_md5(str(media_path)),
        duration_seconds=100,
        status="complete",
        scan_audio=True,
        scan_video=True,
    )
    db.add_all([
        local_scan,
        ScanFindingRow(
            id=2,
            scan_id=local_scan.id,
            category="violence",
            subcategory="violence",
            start_seconds=90,
            end_seconds=100,
            confidence=0.8,
            label="Local",
            source="visual",
            selected=True,
            edit_action="skip",
        ),
    ])
    db.commit()
    local_filter = ReviewFilterInput(**review.filters[0].model_dump()).model_copy(update={
        "id": str(uuid4()),
        "source_scan_id": local_scan.id,
        "source_finding_id": 2,
        "start_seconds": 92,
        "end_seconds": 98,
    })
    saved = repository.save_saved_review(
        db,
        review.id,
        ReviewSave(
            revision=review.revision,
            name=review.name,
            memberships=[
                *review.memberships,
                ReviewScanMembership(scan_id=local_scan.id, role="both"),
            ],
            filters=[ReviewFilterInput(**review.filters[0].model_dump()), local_filter],
            mcf_document=review.mcf_document,
            synchronization=review.synchronization,
        ),
    )
    assert saved is not None

    result = repository.retime_saved_review_draft(db, saved.id, _request(saved))

    assert result is not None
    assert [(value.start_seconds, value.end_seconds) for value in result.filters] == [
        (26, 42), (92, 98)
    ]
    assert result.new_manual_filters[0].start_seconds == 50
    assert result.new_manual_filters[0].end_seconds == 60


def test_retime_treats_scan_without_version_hash_as_unverified(retime_review):
    db, review, _media_path = retime_review
    scan = db.get(ScanRow, "scan")
    assert scan is not None
    scan.origin = "generated"
    scan.video_hash = None
    db.commit()

    result = repository.retime_saved_review_draft(db, review.id, _request(review))

    assert result is not None
    assert (result.filters[0].start_seconds, result.filters[0].end_seconds) == (26, 42)
