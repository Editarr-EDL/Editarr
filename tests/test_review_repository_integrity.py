from pathlib import Path

import pytest

from scanner import repository
from scanner.db import (
    MediaItemExportRow,
    MediaItemRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.models import (
    McfCategoryFamily,
    ReviewCreate,
    ReviewFilterInput,
    ReviewSave,
    ReviewScanMembership,
)
from tests.review_repository_support import review_db


def test_duplicate_source_provenance_is_a_membership_error():
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
        duplicate = created.filters[0].model_copy(
            update={"id": "96561a31-a734-4403-92c2-8b291385e224"}
        )
        with pytest.raises(repository.ReviewMembershipError, match="provenance"):
            repository.save_saved_review(
                db,
                created.id,
                ReviewSave(
                    revision=created.revision,
                    name=created.name,
                    memberships=created.memberships,
                    filters=[
                        ReviewFilterInput(**created.filters[0].model_dump()),
                        ReviewFilterInput(**duplicate.model_dump()),
                    ],
                    mcf_document=created.mcf_document,
                    synchronization=created.synchronization,
                ),
            )
        assert repository.get_saved_review(db, created.id) == created
    finally:
        db.close()


def test_review_name_sort_uses_unicode_casefold_with_id_tiebreaker():
    db = review_db()
    try:
        rows = [
            SavedReviewRow(id="b", media_item_id="media", media_source_id="", name="Straße"),
            SavedReviewRow(id="a", media_item_id="media", media_source_id="", name="STRASSE"),
            SavedReviewRow(id="c", media_item_id="media", media_source_id="", name="apple"),
        ]
        db.add_all(rows)
        db.commit()
        assert [review.id for review in repository.list_saved_reviews(
            db, media_item_id="media", sort="name", direction="asc"
        )] == ["c", "a", "b"]
        assert [review.id for review in repository.list_saved_reviews(
            db, media_item_id="media", sort="name", direction="desc"
        )] == ["a", "b", "c"]
    finally:
        db.close()


def test_membership_requires_same_physical_file_and_null_version_is_scoped(tmp_path):
    db = review_db()
    try:
        first = tmp_path / "first.mkv"
        second = tmp_path / "second.mkv"
        first.touch()
        second.touch()
        db.get(ScanRow, "scan").resolved_local_path = str(first)
        db.add(
            ScanRow(
                id="different-file",
                media_item_id="media",
                scan_number=2,
                source_path=str(second),
                resolved_local_path=str(second),
                status="complete",
                scan_audio=True,
                scan_video=False,
            )
        )
        db.add(
            SavedReviewRow(
                id="versioned-review",
                media_item_id="media",
                media_source_id="v2",
                name="Versioned",
            )
        )
        db.commit()
        with pytest.raises(repository.ReviewMembershipError, match="physical"):
            repository.create_saved_review(
                db,
                media_item_id="media",
                media_source_id=None,
                data=ReviewCreate(
                    name="Cross file",
                    memberships=[
                        ReviewScanMembership(scan_id="scan", role="audio"),
                        ReviewScanMembership(scan_id="different-file", role="audio"),
                    ],
                ),
            )
        db.rollback()
        default = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(name="Default"),
        )
        assert [value.id for value in repository.list_saved_reviews(
            db, media_item_id="media", media_source_id=None
        )] == [default.id]
    finally:
        db.close()


def test_associated_scan_must_match_target_physical_file_at_create_and_export(tmp_path):
    db = review_db()
    try:
        target = tmp_path / "target.mkv"
        other = tmp_path / "other.mkv"
        target.write_bytes(b"target")
        other.write_bytes(b"other")
        media = db.get(MediaItemRow, "media")
        media.path = str(target)
        source_scan = db.get(ScanRow, "scan")
        source_scan.source_path = str(target)
        source_scan.resolved_local_path = str(target)
        associated_media = MediaItemRow(
            id="associated-media",
            server_id="other-server",
            jellyfin_id="other-item",
            item_type="Movie",
            name="Other",
            path=str(other),
        )
        associated_scan = ScanRow(
            id="associated-scan",
            media_item_id=associated_media.id,
            scan_number=1,
            source_path=str(other),
            resolved_local_path=str(other),
            status="complete",
            scan_audio=True,
            scan_video=False,
        )
        db.add_all([associated_media, associated_scan])
        db.flush()
        repository.associate_scans_with_media_item(
            db, media_item_id=media.id, scan_ids=[associated_scan.id]
        )

        with pytest.raises(repository.ReviewMembershipError, match="physical"):
            repository.create_saved_review(
                db,
                media_item_id=media.id,
                media_source_id=None,
                data=ReviewCreate(
                    name="Wrong file",
                    memberships=[
                        ReviewScanMembership(scan_id=associated_scan.id, role="audio")
                    ],
                ),
            )
        db.rollback()

        created = repository.create_saved_review(
            db,
            media_item_id=media.id,
            media_source_id=None,
            data=ReviewCreate(
                name="Target",
                memberships=[ReviewScanMembership(scan_id=source_scan.id, role="audio")],
            ),
        )
        source_scan.resolved_local_path = str(other)
        db.commit()
        with pytest.raises(repository.ReviewMembershipError, match="physical"):
            repository.export_saved_review_edl(db, created.id, created.revision)
        assert not other.with_suffix(".edl").exists()
    finally:
        db.close()


def test_review_filter_provenance_is_required_source_checked_and_immutable():
    db = review_db()
    try:
        source_scan = db.get(ScanRow, "scan")
        source_scan.scan_video = True
        second_scan = ScanRow(
            id="second-scan",
            media_item_id="media",
            scan_number=2,
            source_path=source_scan.source_path,
            resolved_local_path=source_scan.resolved_local_path,
            status="complete",
            scan_audio=True,
            scan_video=False,
        )
        db.add(second_scan)
        db.flush()
        second_finding = ScanFindingRow(
            scan_id=second_scan.id,
            category=McfCategoryFamily.LANGUAGE,
            subcategory="swearing",
            start_seconds=3,
            end_seconds=4,
            confidence=0.8,
            label="Other evidence",
            source="audio",
            selected=True,
            edit_action="mute",
        )
        db.add(second_finding)
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Review",
                memberships=[
                    ReviewScanMembership(scan_id="scan", role="both"),
                    ReviewScanMembership(scan_id=second_scan.id, role="audio"),
                ],
            ),
        )
        original = created.filters[0]
        other = created.filters[1]
        for forged in (
            original.model_copy(update={"source": "visual"}),
            original.model_copy(update={"source_finding_id": other.source_finding_id}),
            original.model_copy(update={"source_scan_id": other.source_scan_id}),
        ):
            with pytest.raises(repository.ReviewMembershipError, match="immutable"):
                repository.save_saved_review(
                    db,
                    created.id,
                    ReviewSave(
                        revision=0,
                        name=created.name,
                        memberships=created.memberships,
                        filters=[
                            ReviewFilterInput(**forged.model_dump()),
                            ReviewFilterInput(**other.model_dump()),
                        ],
                        mcf_document=created.mcf_document,
                        synchronization=created.synchronization,
                    ),
                )
            db.rollback()

        for forged, message in (
            (
                original.model_copy(
                    update={
                        "id": "53a45b09-f3a1-42e7-a055-0011813cf995",
                        "source_finding_id": None,
                    }
                ),
                "required",
            ),
            (
                original.model_copy(
                    update={
                        "id": "7d7367dd-1acd-437f-bbe8-a922495ca23e",
                        "source": "visual",
                    }
                ),
                "provenance",
            ),
        ):
            with pytest.raises(repository.ReviewMembershipError, match=message):
                repository.save_saved_review(
                    db,
                    created.id,
                    ReviewSave(
                        revision=0,
                        name=created.name,
                        memberships=created.memberships,
                        filters=[
                            ReviewFilterInput(**value.model_dump())
                            for value in created.filters
                        ]
                        + [ReviewFilterInput(**forged.model_dump())],
                        mcf_document=created.mcf_document,
                        synchronization=created.synchronization,
                    ),
                )
            db.rollback()
    finally:
        db.close()


def test_export_commit_failure_restores_previous_file_and_provenance(tmp_path, monkeypatch):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(media_path)
        db.commit()
        first = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="First",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        repository.export_saved_review_edl(db, first.id, 0)
        previous_content = media_path.with_suffix(".edl").read_bytes()
        previous_export = db.query(MediaItemExportRow).one()
        previous_exported_at = previous_export.exported_at
        previous_scan_links = [link.scan_id for link in previous_export.scans]
        second = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Second",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(ValueError, match="Unable to export EDL"):
            repository.export_saved_review_edl(db, second.id, 0)
        assert media_path.with_suffix(".edl").read_bytes() == previous_content
        db.expire_all()
        active = db.query(MediaItemExportRow).one()
        assert active.exported_at == previous_exported_at
        assert active.review_id == first.id
        assert active.exported_format_revision == 0
        assert [link.scan_id for link in active.scans] == previous_scan_links
        assert not list(tmp_path.glob(".movie.edl.*.tmp"))
        assert not list(tmp_path.glob(".movie.edl.*.bak"))
    finally:
        db.close()


def test_existing_sidecar_remains_present_until_atomic_replacement(tmp_path, monkeypatch):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        edl_path = media_path.with_suffix(".edl")
        edl_path.write_bytes(b"previous sidecar")
        scan = db.get(ScanRow, "scan")
        scan.source_path = str(media_path)
        scan.resolved_local_path = str(media_path)
        db.commit()
        review = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Replacement",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        real_replace = repository.os.replace

        def assert_continuous_destination(source, destination):
            if Path(destination) == edl_path and str(source).endswith(".tmp"):
                assert edl_path.read_bytes() == b"previous sidecar"
            return real_replace(source, destination)

        monkeypatch.setattr(repository.os, "replace", assert_continuous_destination)
        repository.export_saved_review_edl(db, review.id, review.revision)

        assert edl_path.read_text().startswith("## Editarr Simple EDL")
        assert not list(tmp_path.glob(".movie.edl.*.bak"))
    finally:
        db.close()


def test_restore_failure_preserves_recovery_backup(tmp_path, monkeypatch):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        edl_path = media_path.with_suffix(".edl")
        edl_path.write_bytes(b"known-good sidecar")
        scan = db.get(ScanRow, "scan")
        scan.source_path = str(media_path)
        scan.resolved_local_path = str(media_path)
        db.commit()
        review = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Replacement",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )
        real_replace = repository.os.replace

        def fail_backup_restore(source, destination):
            if str(source).endswith(".bak"):
                raise OSError("injected restore failure")
            return real_replace(source, destination)

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(repository.os, "replace", fail_backup_restore)
        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(ValueError, match="restore the previous EDL"):
            repository.export_saved_review_edl(db, review.id, review.revision)

        backups = list(tmp_path.glob(".movie.edl.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == b"known-good sidecar"
        assert db.query(MediaItemExportRow).count() == 0
        assert not list(tmp_path.glob(".movie.edl.*.tmp"))
    finally:
        db.close()


def test_first_export_commit_failure_removes_sidecar_and_provenance(tmp_path, monkeypatch):
    db = review_db()
    try:
        media_path = tmp_path / "movie.mkv"
        media_path.write_bytes(b"media")
        scan = db.get(ScanRow, "scan")
        scan.resolved_local_path = str(media_path)
        db.commit()
        created = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="First",
                memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
            ),
        )

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(ValueError, match="Unable to export EDL"):
            repository.export_saved_review_edl(db, created.id, 0)

        assert not media_path.with_suffix(".edl").exists()
        assert db.query(MediaItemExportRow).count() == 0
        assert not list(tmp_path.glob(".movie.edl.*.tmp"))
        assert not list(tmp_path.glob(".movie.edl.*.bak"))
    finally:
        db.close()


