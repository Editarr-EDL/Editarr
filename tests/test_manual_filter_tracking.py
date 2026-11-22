"""Review edits retain trustworthy filter provenance through save and publication."""

import uuid

from scanner import repository
from scanner.db import MediaItemRow, ReviewFilterRow, ScanFindingRow, ScanRow
from scanner.formats.community.codec import parse_artifact
from scanner.integrations.community.donation_projection import project_source
from scanner.integrations.community.donation_sources import SourceRef, canonical, capture_source
from scanner.models import (
    EditAction,
    NewManualReviewFilter,
    ReviewCreate,
    ReviewDuplicate,
    ReviewFilterInput,
    ReviewSave,
    ReviewScanMembership,
)
from tests.review_repository_support import review_db
from tests.review_retime_request import _request
from tests.review_retime_support import retime_review as retime_review
from tests.test_community_donations import BOUNDS, options


def _create(db, **values):
    return repository.create_saved_review(
        db,
        media_item_id="media",
        media_source_id=None,
        data=ReviewCreate(name="Tracking review", **values),
    )


def _save(db, review, filters=None, **updates):
    return repository.save_saved_review(
        db,
        review.id,
        ReviewSave(
            **(
                {
                    "revision": review.revision,
                    "name": review.name,
                    "memberships": review.memberships,
                    "filters": [
                        row.model_dump() for row in (review.filters if filters is None else filters)
                    ],
                    "mcf_document": review.mcf_document,
                    "synchronization": review.synchronization,
                    "policy_owner_id": review.policy_owner_id,
                    "policy_revision": review.policy_revision,
                }
                | updates
            )
        ),
    )


def _flags(db, review_id):
    rows = db.query(ReviewFilterRow).filter_by(review_id=review_id).all()
    return {row.source_finding_id: (row.manually_added, row.manually_edited) for row in rows}


def _counts(db, review):
    captured = capture_source(
        db.get_bind(), SourceRef("review", review.id), bounds=BOUNDS, source_limit=4
    )
    artifact, *_ = project_source(
        captured, options(duration_seconds=review.synchronization.end_seconds), bounds=BOUNDS
    )
    return parse_artifact(canonical(artifact), bounds=BOUNDS).summary["manual_filter_counts"]


def test_manual_additions_and_surviving_edits_roll_through_saves_and_duplicate(
    tmp_path, monkeypatch
):
    with review_db() as db:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"video")
        db.get(MediaItemRow, "media").path = str(media_path)
        source_scan = db.get(ScanRow, "scan")
        source_scan.source_path = source_scan.resolved_local_path = str(media_path)
        db.commit()
        monkeypatch.setattr(repository, "get_video_duration", lambda _path: 100.0)
        draft = NewManualReviewFilter(
            id=str(uuid.uuid4()),
            source="visual",
            category="violence",
            subcategory="violence",
            start_seconds=6,
            end_seconds=7,
            edit_action="skip",
            label=None,
            details=None,
            group_key=None,
        )
        review = _create(
            db,
            memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            new_manual_filters=[draft],
        )
        assert _flags(db, review.id) == {1: (False, False), 2: (True, False)}
        assert _counts(db, review) == {"added": 1, "edited": 0}

        generated, manual = review.filters
        generated = generated.model_copy(update={"start_seconds": 1.25})
        changed = _save(db, review, [generated, manual])
        assert _flags(db, review.id) == {1: (False, True), 2: (True, False)}

        generated = generated.model_copy(update={"id": str(uuid.uuid4()), "selected": False})
        manual = manual.model_copy(update={"start_seconds": 6.25})
        saved = _save(db, changed, [generated, manual])
        assert _flags(db, review.id) == {1: (False, True), 2: (True, True)}
        assert _counts(db, saved) == {"added": 1, "edited": 1}
        copied = repository.duplicate_saved_review(
            db,
            saved.id,
            ReviewDuplicate(
                name="Forked tracking review",
                revision=saved.revision,
                policy_revision=saved.policy_revision,
                policy_owner_id=saved.policy_owner_id,
            ),
        )
        assert _flags(db, copied.id) == _flags(db, saved.id)
        assert set(row.id for row in copied.filters).isdisjoint(x.id for x in saved.filters)
        assert _counts(db, copied) == {"added": 1, "edited": 1}


def test_first_save_detects_draft_edits_but_ignores_selection():
    with review_db() as db:
        from scanner.persistence.repository.review_policy import _source_filter

        source = _source_filter(db.get(ScanFindingRow, 1), str(uuid.uuid4()))
        base = ReviewFilterInput.model_validate(source.model_dump())
        untouched = _create(
            db,
            memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            filters=[base.model_copy(update={"selected": False})],
        )
        drafted = _create(
            db,
            memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            filters=[base.model_copy(update={"edit_action": EditAction.SKIP})],
        )
        assert _flags(db, untouched.id) == {1: (False, False)}
        assert _flags(db, drafted.id) == {1: (False, True)}


def test_pure_retime_keeps_legacy_unknown_and_draft_adjustment_is_recorded(retime_review):
    db, review, _ = retime_review
    assert _flags(db, review.id) == {1: (None, None)}
    retimed = repository.retime_saved_review_draft(
        db, review.id, _request(review, new_manual_filters=[])
    )
    saved = _save(
        db,
        review,
        [retimed.filters[0].model_copy(update={"selected": True})],
        synchronization=retimed.synchronization,
        mcf_document=retimed.mcf_document,
    )
    assert _flags(db, review.id) == {1: (None, None)}
    assert _counts(db, saved) == {"added": None, "edited": None}
    draft = saved.filters[0].model_copy(update={"start_seconds": 25.5})
    _save(db, saved, [draft])
    assert _flags(db, review.id) == {1: (None, True)}


def test_saved_filters_report_label_edits_separately_from_other_edits():
    with review_db() as db:
        review = _create(db, memberships=[ReviewScanMembership(scan_id="scan", role="audio")])
        [generated] = review.filters
        source_label = db.get(ScanFindingRow, 1).label
        assert generated.label == source_label and generated.label_edited is False

        # Another field changed: the filter is manually edited, but its label is not.
        retimed = _save(db, review, [generated.model_copy(update={"start_seconds": 1.25})])
        assert retimed.filters[0].label_edited is False
        assert _flags(db, review.id) == {1: (False, True)}

        relabelled = _save(
            db, retimed, [retimed.filters[0].model_copy(update={"label": "my own label"})]
        )
        assert relabelled.filters[0].label == "my own label"
        assert relabelled.filters[0].label_edited is True
        # Clients echo the flag back; it is read-only and never stored or trusted.
        echoed = _save(
            db,
            relabelled,
            [
                relabelled.filters[0].model_copy(
                    update={"label": source_label, "label_edited": True}
                )
            ],
        )
        assert echoed.filters[0].label_edited is False
        assert "label_edited" not in ReviewFilterInput.model_validate(
            echoed.filters[0].model_dump()
        ).model_dump()
