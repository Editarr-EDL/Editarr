from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import api, repository
from scanner.db import (
    Base,
    MediaItemExportRow,
    MediaItemExportScanRow,
    MediaItemRow,
    ScanRow,
)
from scanner.models import (
    ExportFormat,
    JellyfinMediaItem,
    JellyfinMediaVersion,
    ReviewCreate,
    ReviewSave,
    ReviewScanMembership,
)
from scanner.persistence.repository.review_deletion import PublishedReviewDeletionBlocked


def test_formats_coexist_stale_independently_and_delete_independently(tmp_path):
    db, review, scan, media_path = _export_db(tmp_path)
    try:
        _seed_export(db, review.id, scan.id, ExportFormat.EDL, 0)
        _seed_export(db, review.id, scan.id, ExportFormat.MCF, 0)
        media_path.with_suffix(".edl").write_text("edl")
        media_path.with_suffix(".mcf").write_text("mcf")

        status = repository.media_item_export_status(
            db,
            server_id="server",
            jellyfin_item_id="item",
            media_source_id="version-1",
        )
        assert status.exports[ExportFormat.EDL].current is True
        assert status.exports[ExportFormat.MCF].current is True
        assert status.exports[ExportFormat.EDL].exported_review is not None

        changed = repository.save_saved_review(
            db,
            review.id,
            ReviewSave(
                revision=0,
                name=review.name,
                memberships=review.memberships,
                filters=[],
                mcf_document=review.mcf_document.model_copy(
                    update={"comment": "MCF-only change"}
                ),
                synchronization=review.synchronization,
            ),
        )
        assert changed.edl_content_revision == 0
        assert changed.mcf_content_revision == 1
        status = repository.media_item_export_status(
            db,
            server_id="server",
            jellyfin_item_id="item",
            media_source_id="version-1",
        )
        assert status.exports[ExportFormat.EDL].current is True
        assert status.exports[ExportFormat.MCF].outdated is True

        assert repository.delete_library_item_export(
            db,
            server_id="server",
            jellyfin_item_id="item",
            media_source_id="version-1",
            export_format=ExportFormat.EDL,
        )
        assert not media_path.with_suffix(".edl").exists()
        assert media_path.with_suffix(".mcf").read_text() == "mcf"
        remaining = repository.media_item_export_status(
            db,
            server_id="server",
            jellyfin_item_id="item",
            media_source_id="version-1",
        )
        assert remaining.exports[ExportFormat.EDL].present is False
        assert remaining.exports[ExportFormat.MCF].present is True
    finally:
        db.close()


def test_published_review_delete_preserves_both_files_and_attribution(tmp_path):
    db, review, scan, media_path = _export_db(tmp_path)
    try:
        for export_format in ExportFormat:
            _seed_export(db, review.id, scan.id, export_format, 0)
            media_path.with_suffix(f".{export_format.value}").write_text(
                export_format.value
            )

        with pytest.raises(PublishedReviewDeletionBlocked):
            repository.delete_saved_review(db, review.id, 0)
        assert repository.get_saved_review(db, review.id) is not None

        status = repository.media_item_export_status(
            db,
            server_id="server",
            jellyfin_item_id="item",
            media_source_id="version-1",
        )
        for export_format in ExportFormat:
            summary = status.exports[export_format]
            assert summary.present is True
            assert summary.review_id == review.id
            assert summary.exported_format_revision == 0
            assert summary.current is True
            assert summary.outdated is False
            assert [reference.id for reference in summary.exported_scans] == [scan.id]
            assert media_path.with_suffix(f".{export_format.value}").exists()
        references = repository.get_scan_references(db, scan.id)
        assert references.active_exports == [ExportFormat.EDL, ExportFormat.MCF]
        with pytest.raises(repository.ScanReferencedError) as error:
            repository.delete_scan(db, scan.id)
        assert error.value.references.active_exports == [
            ExportFormat.EDL,
            ExportFormat.MCF,
        ]
        assert "EDL, MCF" in str(error.value)
    finally:
        db.close()


def test_version_statuses_do_not_merge_physical_versions(tmp_path):
    db, review, scan, _media_path = _export_db(tmp_path)
    try:
        _seed_export(db, review.id, scan.id, ExportFormat.EDL, 0)
        second_scan = ScanRow(
            id="scan-2",
            media_item_id="media",
            scan_number=2,
            source_path=str(tmp_path / "movie-2.mkv"),
            resolved_local_path=str(tmp_path / "movie-2.mkv"),
            media_source_id="version-2",
            status="complete",
            scan_audio=False,
            scan_video=True,
        )
        db.add(second_scan)
        db.flush()
        db.add(
            MediaItemExportRow(
                media_item_id="media",
                media_source_id="version-2",
                format=ExportFormat.MCF,
                exported_at=datetime.now(timezone.utc) + timedelta(seconds=1),
                scans=[
                    MediaItemExportScanRow(scan_id=second_scan.id, role="video")
                ],
            )
        )
        db.commit()

        statuses = repository.library_item_version_export_statuses(
            db, server_id="server", jellyfin_item_id="item"
        )

        assert set(statuses) == {"version-1", "version-2"}
        assert statuses["version-1"].exports[ExportFormat.EDL].present is True
        assert statuses["version-1"].exports[ExportFormat.MCF].present is False
        assert statuses["version-2"].exports[ExportFormat.EDL].present is False
        assert statuses["version-2"].exports[ExportFormat.MCF].present is True
    finally:
        db.close()


def test_delete_commit_failure_restores_file_and_database_row(tmp_path, monkeypatch):
    db, review, scan, media_path = _export_db(tmp_path)
    try:
        _seed_export(db, review.id, scan.id, ExportFormat.MCF, 0)
        destination = media_path.with_suffix(".mcf")
        destination.write_text("known-good")

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(ValueError, match="Unable to delete MCF export"):
            repository.delete_library_item_export(
                db,
                server_id="server",
                jellyfin_item_id="item",
                media_source_id="version-1",
                export_format=ExportFormat.MCF,
            )

        assert destination.read_text() == "known-good"
        assert db.query(MediaItemExportRow).count() == 1
        assert not list(tmp_path.glob(".movie.mcf.*.delete"))
    finally:
        db.close()


def test_delete_filesystem_failure_retains_matching_database_row(tmp_path, monkeypatch):
    db, review, scan, media_path = _export_db(tmp_path)
    try:
        _seed_export(db, review.id, scan.id, ExportFormat.MCF, 0)
        destination = media_path.with_suffix(".mcf")
        destination.write_text("known-good")

        def fail_replace(_source, _destination):
            raise OSError("read only")

        monkeypatch.setattr(repository.os, "replace", fail_replace)
        with pytest.raises(ValueError, match="Unable to delete MCF export"):
            repository.delete_library_item_export(
                db,
                server_id="server",
                jellyfin_item_id="item",
                media_source_id="version-1",
                export_format=ExportFormat.MCF,
            )

        assert destination.read_text() == "known-good"
        assert db.query(MediaItemExportRow).count() == 1
    finally:
        db.close()


def test_catalog_decoration_exposes_independent_format_statuses(tmp_path):
    db, review, scan, _media_path = _export_db(tmp_path)
    try:
        _seed_export(db, review.id, scan.id, ExportFormat.MCF, 0)
        item = JellyfinMediaItem(
            id="item",
            item_type="Movie",
            name="Movie",
            versions=[JellyfinMediaVersion(id="version-1")],
        )

        decorated = api._with_library_item_state(
            db, server_id="server", items=[item]
        )

        assert len(decorated) == 1
        assert decorated[0].exports.exports[ExportFormat.MCF].current is True
        assert decorated[0].exports.exports[ExportFormat.EDL].present is False
        assert decorated[0].versions[0].exports.exports[ExportFormat.MCF].current is True
        assert decorated[0].versions[0].exports.exports[ExportFormat.EDL].present is False
    finally:
        db.close()


def _export_db(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"media")
    media = MediaItemRow(
        id="media",
        server_id="server",
        jellyfin_id="item",
        item_type="Movie",
        name="Movie",
        path=str(media_path),
    )
    scan = ScanRow(
        id="scan",
        media_item_id="media",
        scan_number=1,
        source_path=str(media_path),
        resolved_local_path=str(media_path),
        media_source_id="version-1",
        duration_seconds=100,
        status="complete",
        scan_audio=True,
        scan_video=False,
    )
    db.add_all([media, scan])
    db.commit()
    review = repository.create_saved_review(
        db,
        media_item_id="media",
        media_source_id="version-1",
        data=ReviewCreate(
            name="Review",
            memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
        ),
    )
    return db, review, scan, media_path


def _seed_export(db, review_id, scan_id, export_format, revision):
    db.add(
        MediaItemExportRow(
            media_item_id="media",
            media_source_id="version-1",
            format=export_format,
            exported_at=datetime.now(timezone.utc),
            review_id=review_id,
            exported_format_revision=revision,
            scans=[MediaItemExportScanRow(scan_id=scan_id, role="audio")],
        )
    )
    db.commit()
