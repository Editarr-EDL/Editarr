from datetime import timedelta

import pytest

from scanner import repository
from scanner.db import (
    MediaItemRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.models import (
    EditAction,
    ExportFormat,
    McfCategoryFamily,
    McfChannel,
    NewManualReviewFilter,
    ReviewCreate,
    ReviewFilterInput,
    ReviewRename,
    ReviewSave,
    ReviewScanMembership,
    Severity,
)
from tests.review_repository_support import review_db


def test_saved_review_export_uses_snapshot_and_records_provenance(tmp_path):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        scan = db.get(ScanRow, "scan")
        scan.source_path = str(media_path)
        scan.resolved_local_path = str(media_path)
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Export me",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        result = repository.export_saved_review_edl(db, created.id, 0)
        assert result is not None
        assert result.exported_review.exports.exports[ExportFormat.EDL].current is True
        assert "## Editarr Simple EDL" in media_path.with_suffix(".edl").read_text()
        edl = db.query(repository.MediaItemExportRow).one()
        assert edl.review_id == created.id
        assert edl.exported_format_revision == 0
        assert [reference.id for reference in result.exported_scans] == ["scan"]
    finally:
        db.close()


def test_export_response_reports_the_revision_written_before_a_concurrent_change(
    tmp_path, monkeypatch
):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        scan = db.get(ScanRow, "scan")
        scan.source_path = str(media_path)
        scan.resolved_local_path = str(media_path)
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Export me",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        real_commit = db.commit

        def commit_after_review_change():
            row = db.get(SavedReviewRow, created.id)
            row.filters[0].label = "Changed during export"
            row.revision += 1
            row.content_revision += 1
            row.edl_content_revision += 1
            real_commit()

        monkeypatch.setattr(db, "commit", commit_after_review_change)
        result = repository.export_saved_review_edl(db, created.id, created.revision)

        assert result is not None
        assert result.exported_format_revision == 0
        assert (
            result.exported_review.exports.exports[ExportFormat.EDL].exported_format_revision
            == 0
        )
        assert result.exported_review.content_revision == 1
        assert result.exported_review.exports.exports[ExportFormat.EDL].outdated is True
    finally:
        db.close()


def test_legacy_internal_mutation_and_export_helpers_are_removed():
    assert not hasattr(repository, "export_scan_edl")
    assert not hasattr(repository, "add_manual_scan_finding")
    assert not hasattr(repository, "render_scan_finding_mute_preview")
    assert not hasattr(repository, "render_scan_finding_video_preview")


def test_review_export_uses_only_selected_snapshots_and_reports_outdated(tmp_path):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(media_path)
        scan.source_path = str(media_path)
        db.add(
            ScanFindingRow(
                scan_id="scan",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=3,
                end_seconds=4,
                confidence=1,
                label="Not selected",
                source="audio",
                selected=False,
                edit_action="mute",
            )
        )
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Selected only",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        exported = repository.export_saved_review_edl(db, created.id, 0)
        content = media_path.with_suffix(".edl").read_text()
        assert "Evidence" in content
        assert "Not selected" not in content
        db.expire_all()
        statuses = [
            repository.media_item_export_status(
                db, server_id="server", jellyfin_item_id="item"
            ).exports[ExportFormat.EDL],
            repository.library_item_export_statuses(
                db, server_id="server", jellyfin_item_ids=["item"]
            )["item"].exports[ExportFormat.EDL],
            repository.library_item_version_export_statuses(
                db, server_id="server", jellyfin_item_id="item"
            )[""].exports[ExportFormat.EDL],
        ]
        for status in statuses:
            assert status.exported_at is not None
            assert status.exported_at.utcoffset() == timedelta(0)
            assert status.review_id == created.id
            assert status.exported_format_revision == 0
            assert status.exported_review is not None
            assert status.exported_review.exports.exports[ExportFormat.EDL].outdated is False

        changed = created.filters[0].model_copy(update={"selected": False})
        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=0,
                name=created.name,
                memberships=created.memberships,
                filters=[
                    ReviewFilterInput(**changed.model_dump()),
                    ReviewFilterInput(**created.filters[1].model_dump()),
                ],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )
        assert saved is not None
        db.expire_all()
        statuses = [
            repository.media_item_export_status(
                db, server_id="server", jellyfin_item_id="item"
            ).exports[ExportFormat.EDL],
            repository.library_item_export_statuses(
                db, server_id="server", jellyfin_item_ids=["item"]
            )["item"].exports[ExportFormat.EDL],
            repository.library_item_version_export_statuses(
                db, server_id="server", jellyfin_item_id="item"
            )[""].exports[ExportFormat.EDL],
        ]
        for status in statuses:
            assert status.review_id == created.id
            assert status.exported_format_revision == 0
            assert status.exported_review is not None
            assert status.exported_review.exports.exports[ExportFormat.EDL].outdated is True
        assert exported is not None

        repository.export_saved_review_edl(db, created.id, saved.revision)
        assert media_path.with_suffix(".edl").read_text() == "## Editarr Simple EDL"
    finally:
        db.close()


def test_mcf_only_edit_does_not_make_edl_export_outdated(tmp_path):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(media_path)
        scan.source_path = str(media_path)
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
        repository.export_saved_review_edl(db, created.id, created.revision)

        changed_filter = ReviewFilterInput(
            **(
                created.filters[0].model_dump()
                | {"severity": Severity.HIGH, "mcf_comment": "MCF only"}
            )
        )
        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=created.revision,
                name=created.name,
                memberships=created.memberships,
                filters=[changed_filter],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )

        assert saved.edl_content_revision == 0
        assert saved.mcf_content_revision == 1
        assert saved.exports.exports[ExportFormat.EDL].current is True
    finally:
        db.close()


def test_mcf_only_edit_preserves_audio_preview_source_action_and_range(
    tmp_path, monkeypatch
):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(media_path)
        scan.source_path = str(media_path)
        db.get(MediaItemRow, "media").path = str(media_path)
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
        changed_filter = ReviewFilterInput(
            **(
                created.filters[0].model_dump()
                | {
                    "severity": Severity.HIGH,
                    "mcf_channel": McfChannel.VIDEO,
                    "mcf_comment": "Video classification does not change evidence",
                }
            )
        )
        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=0,
                name=created.name,
                memberships=created.memberships,
                filters=[changed_filter],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
            ),
        )
        calls = []
        monkeypatch.setattr(
            repository,
            "render_mute_preview",
            lambda path, start, end: (
                calls.append((str(path), start, end)) or b"preview"
            ),
        )

        assert repository.render_saved_review_filter_preview(
            db, saved.id, saved.filters[0].id, preview_kind="mute"
        ) == b"preview"
        assert calls == [(str(media_path), 1, 2)]
    finally:
        db.close()


def test_manual_filter_creation_atomically_adds_membership_and_exact_snapshot(tmp_path):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        media = db.get(MediaItemRow, "media")
        media.path = str(media_path)
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(media_path)
        db.commit()
        staged = NewManualReviewFilter(
            id="0c801c22-ceac-49d6-8111-2a512ae4bf4c",
            source="visual",
            category=McfCategoryFamily.VIOLENCE,
            subcategory="violence",
            label="Manual scene",
            details=None,
            group_key=None,
            start_seconds=5,
            end_seconds=6,
            edit_action=EditAction.SKIP,
        )
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(name="Manual draft", new_manual_filters=[staged]),
        )
        assert len(created.memberships) == 1
        assert created.memberships[0].role == "video"
        assert len(created.filters) == 1
        assert created.filters[0].id == staged.id
        assert created.filters[0].source_finding_id is not None
        manual_scan = db.get(ScanRow, created.memberships[0].scan_id)
        assert manual_scan is not None
        assert manual_scan.origin == "manual"
        assert manual_scan.scan_video is True
    finally:
        db.close()


def test_manual_filter_save_adds_membership_and_preserves_existing_snapshots(tmp_path):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        media = db.get(MediaItemRow, "media")
        media.path = str(media_path)
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(media_path)
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
        staged = NewManualReviewFilter(
            id="b8717134-08e1-431f-b0d8-7dca462035db",
            source="visual",
            category=McfCategoryFamily.VIOLENCE,
            subcategory="violence",
            label="Manual scene",
            details="Added later",
            group_key=None,
            start_seconds=5,
            end_seconds=6,
            edit_action=EditAction.SKIP,
        )

        saved = repository.save_saved_review(
            db,
            created.id,
            ReviewSave(
                revision=created.revision,
                name=created.name,
                memberships=created.memberships,
                filters=[ReviewFilterInput(**value.model_dump()) for value in created.filters],
                mcf_document=created.mcf_document,
                synchronization=created.synchronization,
                new_manual_filters=[staged],
            ),
        )

        assert saved is not None
        assert saved.revision == 1
        assert saved.content_revision == 1
        assert {(value.scan_id, value.role) for value in saved.memberships} == {
            ("scan", "audio"),
            (saved.filters[1].source_scan_id, "video"),
        }
        assert [value.id for value in saved.filters] == [created.filters[0].id, staged.id]
        assert saved.filters[1].source_finding_id is not None
        manual_scan = db.get(ScanRow, saved.filters[1].source_scan_id)
        assert manual_scan is not None
        assert manual_scan.origin == "manual"
        assert manual_scan.scan_video is True
    finally:
        db.close()


def test_failed_manual_staging_rolls_back_without_caller_cleanup(tmp_path):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        db.get(MediaItemRow, "media").path = str(media_path)
        db.get(ScanRow, "scan").resolved_local_path = str(media_path)
        db.commit()
        staged = NewManualReviewFilter(
            id="d2e5f572-e8a4-4fcf-a263-df25173258a4",
            source="visual",
            category=McfCategoryFamily.VIOLENCE,
            subcategory="violence",
            label="Must roll back",
            details=None,
            group_key=None,
            start_seconds=5,
            end_seconds=6,
            edit_action=EditAction.SKIP,
        )

        with pytest.raises(repository.ReviewMembershipError):
            repository.create_saved_review(
                db,
                media_item_id="media",
                media_source_id=None,
                data=ReviewCreate(
                    name="Invalid create",
                    memberships=[ReviewScanMembership(scan_id="missing", role="audio")],
                    new_manual_filters=[staged],
                ),
            )

        # A later successful repository commit must not persist rows staged by
        # the failed call, even when the caller did not explicitly roll back.
        clean = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(name="Clean"),
        )
        assert clean.name == "Clean"
        assert db.query(ScanRow).filter(ScanRow.origin == "manual").count() == 0
        assert db.query(ScanFindingRow).filter(
            ScanFindingRow.label == "Must roll back"
        ).count() == 0

        with pytest.raises(repository.ReviewMembershipError):
            repository.save_saved_review(
                db,
                clean.id,
                ReviewSave(
                    revision=clean.revision,
                    name=clean.name,
                    memberships=[ReviewScanMembership(scan_id="missing", role="audio")],
                    filters=[],
                    mcf_document=clean.mcf_document,
                    synchronization=clean.synchronization,
                    new_manual_filters=[staged],
                ),
            )
        renamed = repository.rename_saved_review(
            db, clean.id, ReviewRename(revision=clean.revision, name="Still clean")
        )
        assert renamed is not None and renamed.name == "Still clean"
        assert db.query(ScanRow).filter(ScanRow.origin == "manual").count() == 0
        assert db.query(ScanFindingRow).filter(
            ScanFindingRow.label == "Must roll back"
        ).count() == 0
    finally:
        db.close()


