
import pytest

from scanner import repository
from scanner.db import (
    ScanFindingRow,
    ScanRow,
)
from scanner.models import (
    EditAction,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    McfWorkType,
    ReviewCreate,
    ReviewFilterInput,
    ReviewSave,
    ReviewScanMembership,
    Severity,
)
from tests.review_repository_support import review_db


def test_saved_review_snapshots_and_revisions_are_isolated():
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Review",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        assert created.revision == 0
        assert created.content_revision == 0
        assert created.filters[0].label == "Evidence"

        expected_filter = {
            "id": created.filters[0].id,
            "source_scan_id": "scan",
            "source_finding_id": 1,
            "source": "audio",
            "category": None,
            "subcategory": None,
            "severity": "high",
            "mcf_channel": "both",
            "mcf_comment": "Reviewer note",
            "label": "Private",
            "details": None,
            "group_key": "private-group",
            "start_seconds": 3.0,
            "end_seconds": None,
            "confidence": None,
            "edit_action": "skip",
            "selected": False,
        }
        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=0,
                name="Review",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
                filters=[ReviewFilterInput(**expected_filter)],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )
        assert saved.revision == 1
        assert saved.content_revision == 1
        assert saved.filters[0].model_dump(mode="json") == expected_filter | {
            "label_edited": True  # "Private" replaced the finding's "Evidence"
        }
        assert db.get(ScanFindingRow, 1).label == "Evidence"
    finally:
        db.close()


def test_unrelated_scan_duration_does_not_change_review_runtime_validation():
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Review",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        db.add(
            ScanRow(
                id="unrelated-short-scan",
                media_item_id="media",
                scan_number=2,
                source_path="/tmp/other.mkv",
                resolved_local_path="/tmp/other.mkv",
                status="complete",
                duration_seconds=10,
                scan_audio=True,
                scan_video=False,
            )
        )
        db.commit()

        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=created.revision,
                name=created.name,
                memberships=created.memberships,
                filters=[
                    ReviewFilterInput(**review_filter.model_dump())
                    for review_filter in created.filters
                ],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )

        assert saved.synchronization.end_seconds == 100
        assert saved.revision == created.revision
    finally:
        db.close()


@pytest.mark.parametrize(
    ("filter_changes", "document_changes", "expected"),
    [
        ({"label": "Private"}, {}, (1, 1, 1, 0)),
        ({"details": "Private detail"}, {}, (1, 1, 0, 0)),
        ({"group_key": "private-group"}, {}, (1, 1, 0, 0)),
        ({"confidence": 0.25}, {}, (1, 1, 0, 0)),
        ({"severity": Severity.HIGH}, {}, (1, 1, 0, 1)),
        ({"mcf_channel": McfChannel.BOTH}, {}, (1, 1, 0, 1)),
        ({"mcf_comment": "Comment"}, {}, (1, 1, 0, 1)),
        ({"start_seconds": 1.25}, {}, (1, 1, 1, 1)),
        ({"end_seconds": 2.25}, {}, (1, 1, 1, 1)),
        ({"selected": False}, {}, (1, 1, 1, 1)),
        ({"edit_action": EditAction.SKIP}, {}, (1, 1, 1, 0)),
        (
            {
                "category": McfCategoryFamily.VIOLENCE,
                "subcategory": McfCategory.violence,
            },
            {},
            (1, 1, 1, 1),
        ),
        ({}, {"title": "Edited title"}, (1, 1, 0, 1)),
        ({}, {"year": 2020}, (1, 1, 0, 1)),
        ({}, {"work_type": McfWorkType.SERIES}, (1, 1, 0, 1)),
        ({}, {"season": 2}, (1, 1, 0, 1)),
        ({}, {"episode": 3}, (1, 1, 0, 1)),
        ({}, {"source_url": "https://example.com/work"}, (1, 1, 0, 1)),
        ({}, {"imdb_url": "https://www.imdb.com/title/tt1/"}, (1, 1, 0, 1)),
        ({}, {"release": "Director's cut"}, (1, 1, 0, 1)),
        ({}, {"comment": "Review note"}, (1, 1, 0, 1)),
        ({}, {"material_start_seconds": 0.5}, (1, 1, 0, 1)),
        ({}, {"material_end_seconds": 20}, (1, 1, 0, 1)),
    ],
)
def test_review_save_uses_field_specific_revision_matrix(
    filter_changes, document_changes, expected
):
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Review",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        source_filter = ReviewFilterInput(**created.filters[0].model_dump())
        changed_filter = source_filter.model_copy(update=filter_changes)
        changed_document = created.mcf_document.model_copy(update=document_changes)
        changed_synchronization = created.synchronization.model_copy(
            update={
                "start_seconds": document_changes.get(
                    "material_start_seconds", created.synchronization.start_seconds
                )
            }
        )

        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=created.revision,
                name=created.name,
                memberships=created.memberships,
                filters=[changed_filter],
                mcf_document=changed_document,
                synchronization=changed_synchronization,
            ),
        )

        assert saved is not None
        assert (
            saved.revision,
            saved.content_revision,
            saved.edl_content_revision,
            saved.mcf_content_revision,
        ) == expected
    finally:
        db.close()


def test_name_only_combined_and_noop_saves_increment_each_revision_at_most_once():
    db = review_db()
    try:
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Review",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        inputs = [ReviewFilterInput(**created.filters[0].model_dump())]
        renamed = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=0,
                name="Renamed",
                memberships=created.memberships,
                filters=inputs,
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )
        assert (
            renamed.revision,
            renamed.content_revision,
            renamed.edl_content_revision,
            renamed.mcf_content_revision,
        ) == (1, 0, 0, 0)

        unchanged_time = renamed.updated_at
        no_op = repository.save_saved_review(
            db,
            renamed.id,
            ReviewSave(
                revision=1,
                name=renamed.name,
                memberships=renamed.memberships,
                filters=[ReviewFilterInput(**renamed.filters[0].model_dump())],
                mcf_document=renamed.mcf_document,
                synchronization=renamed.synchronization,
            ),
        )
        assert no_op.updated_at == unchanged_time
        assert no_op.revision == 1

        combined_filter = ReviewFilterInput(
            **(
                no_op.filters[0].model_dump()
                | {
                    "label": "Combined",
                    "severity": Severity.HIGH,
                    "selected": False,
                }
            )
        )
        combined = repository.save_saved_review(
            db,
            no_op.id,
            ReviewSave(
                revision=1,
                name="Combined name",
                memberships=no_op.memberships,
                filters=[combined_filter],
                mcf_document=no_op.mcf_document.model_copy(
                    update={"comment": "Combined metadata"}
                ),
                synchronization=no_op.synchronization,
            ),
        )
        assert (
            combined.revision,
            combined.content_revision,
            combined.edl_content_revision,
            combined.mcf_content_revision,
        ) == (2, 1, 1, 1)
    finally:
        db.close()


def test_filter_order_and_membership_role_increment_both_format_revisions():
    db = review_db()
    try:
        scan = db.get(ScanRow, "scan")
        scan.scan_video = True
        db.add(
            ScanFindingRow(
                scan_id="scan",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=3,
                end_seconds=4,
                confidence=0.8,
                label="Second",
                source="audio",
                selected=True,
                edit_action="mute",
            )
        )
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Review",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        reordered = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=0,
                name=created.name,
                memberships=created.memberships,
                filters=[
                    ReviewFilterInput(**review_filter.model_dump())
                    for review_filter in reversed(created.filters)
                ],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )
        assert (
            reordered.content_revision,
            reordered.edl_content_revision,
            reordered.mcf_content_revision,
        ) == (1, 1, 1)

        role_changed = repository.save_saved_review(
            db,
            reordered.id,
            ReviewSave(
                revision=1,
                name=reordered.name,
                memberships=[ReviewScanMembership(scan_id="scan", role="both")],
                filters=[
                    ReviewFilterInput(**review_filter.model_dump())
                    for review_filter in reordered.filters
                ],
                mcf_document=reordered.mcf_document,
                synchronization=reordered.synchronization,
            ),
        )
        assert (
            role_changed.content_revision,
            role_changed.edl_content_revision,
            role_changed.mcf_content_revision,
        ) == (2, 2, 2)
    finally:
        db.close()


