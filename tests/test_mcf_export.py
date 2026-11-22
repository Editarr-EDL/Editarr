from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import (
    Base,
    MediaItemExportRow,
    MediaItemExportScanRow,
    MediaItemRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.models import (
    ExportFormat,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    ReviewCreate,
    ReviewScanMembership,
    Severity,
)


def test_preflight_reports_ordered_persisted_issues_without_mutation(tmp_path):
    db, review, source = _review_with_filter(tmp_path, classified=False, duration=None)
    try:
        before = (source.category, source.subcategory, source.severity, source.mcf_channel)

        preflight = repository.preflight_saved_review_mcf(db, review.id)

        assert preflight.eligible is False
        assert preflight.revision == 0
        assert [(issue.code, issue.field, issue.filter_position) for issue in preflight.issues] == [
            ("invalid_material_bounds", "material_end_seconds", None),
            ("missing_category", "category", 0),
            ("missing_subcategory", "subcategory", 0),
            ("missing_severity", "severity", 0),
        ]
        assert all(issue.filter_id is None for issue in preflight.issues[:1])
        assert all(issue.filter_id is not None for issue in preflight.issues[1:])
        assert before == (
            source.category,
            source.subcategory,
            source.severity,
            source.mcf_channel,
        )
    finally:
        db.close()


def test_preflight_allows_empty_selection_with_valid_bounds(tmp_path):
    db, review, _source = _review_with_filter(tmp_path, classified=False, duration=10)
    try:
        row = db.get(repository.SavedReviewRow, review.id)
        row.filters[0].selected = False
        db.commit()

        preflight = repository.preflight_saved_review_mcf(db, review.id)

        assert preflight.eligible is True
        assert preflight.issues == []
        exported = repository.export_saved_review_mcf(db, review.id, 0)
        assert exported.exported_format_revision == 0
        assert (tmp_path / "movie.mcf").read_bytes() == (
            b"WEBVTT MovieContentFilter 1.1.0\n\n"
            b"NOTE\nTITLE Movie\nTYPE movie\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:10.000\n\n"
        )
    finally:
        db.close()


def test_mcf_export_writes_exact_grouped_bytes_and_generic_provenance(tmp_path):
    db, review, media_path = _exportable_review(tmp_path)
    try:
        row = db.get(repository.SavedReviewRow, review.id)
        row.filters[0].mcf_channel = None
        row.filters[0].details = "Saved\nDescription"
        row.filters[1].details = "Original description"
        row.filters[1].mcf_comment = "Authored comment"
        row.filters[2].mcf_channel = None
        db.commit()
        exported = repository.export_saved_review_mcf(db, review.id, 0)

        assert exported.format == ExportFormat.MCF
        assert exported.exported_format_revision == 0
        assert [reference.id for reference in exported.exported_scans] == ["scan"]
        assert media_path.with_suffix(".mcf").read_bytes() == (
            b"WEBVTT MovieContentFilter 1.1.0\n\n"
            b"NOTE\nTITLE Movie\nTYPE movie\n\n"
            b"NOTE\nSTART 00:00:00.000\nEND 00:00:10.000\n\n"
            b"00:00:01.000 --> 00:00:03.000\n"
            b"swearing=high=audio # Saved Description\n"
            b"violence=medium=video # Authored comment\n\n"
            b"00:00:02.000 --> 00:00:04.000\n"
            b"nudity=low=video\n\n"
        )
        assert not media_path.with_suffix(".edl").exists()
        status = repository.media_item_export_status(
            db,
            server_id="server",
            jellyfin_item_id="item",
        )
        assert status.exports[ExportFormat.MCF].current is True
        assert status.exports[ExportFormat.EDL].present is False
    finally:
        db.close()


def test_preflight_defensively_orders_all_invalid_filter_fields():
    invalid = SimpleNamespace(
        id="4d4e5a70-d86a-4323-bcd3-86272c9eb1a8",
        category="unknown",
        subcategory="unknown",
        severity="critical",
        source="audio",
        details=None,
        mcf_channel="speech",
        mcf_comment="bad --> comment",
        start_seconds=2,
        end_seconds=None,
    )
    issues = repository._mcf_filter_issues(invalid, 3)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("invalid_category", "category"),
        ("invalid_subcategory", "subcategory"),
        ("invalid_severity", "severity"),
        ("invalid_mcf_channel", "mcf_channel"),
        ("invalid_mcf_comment", "mcf_comment"),
        ("invalid_material_bounds", "end_seconds"),
    ]
    pair = SimpleNamespace(
        **{
            **invalid.__dict__,
            "category": McfCategoryFamily.NUDITY,
            "subcategory": McfCategory.swearing,
            "severity": Severity.MEDIUM,
            "mcf_channel": None,
            "mcf_comment": None,
            "end_seconds": 3,
        }
    )
    assert [issue.code for issue in repository._mcf_filter_issues(pair, 0)] == [
        "invalid_category_pair"
    ]


def test_preflight_orders_all_document_fields_without_reflecting_values():
    secret = "/private/scanner-host/secret"
    document = SimpleNamespace(
        title=" ",
        year=-1,
        work_type="clip",
        season=0,
        episode=0,
        source_url=f"file://{secret}",
        imdb_url="https://example.com/not-imdb",
        release="bad\nrelease",
        comment="",
        material_start_seconds=None,
        material_end_seconds=None,
    )

    issues = repository._mcf_document_issues(document, [])

    assert [issue.field for issue in issues] == [
        "title",
        "year",
        "work_type",
        "season",
        "episode",
        "source_url",
        "imdb_url",
        "release",
        "comment",
        "material_start_seconds",
        "material_end_seconds",
    ]
    assert secret not in " ".join(issue.message for issue in issues)


def test_preflight_uses_serialized_mcf_metadata_field_names_for_line_limits():
    prefix = "https://example.com/"
    exact_source = prefix + "a" * (
        repository.MAX_LINE_CODEPOINTS - len("SOURCE ") - len(prefix)
    )
    too_long_source = exact_source + "a"

    exact_issues = repository._mcf_document_issues(
        SimpleNamespace(
            source_url=exact_source,
            material_start_seconds=0,
            material_end_seconds=1,
        ),
        [],
    )
    oversized_issues = repository._mcf_document_issues(
        SimpleNamespace(
            source_url=too_long_source,
            material_start_seconds=0,
            material_end_seconds=1,
        ),
        [],
    )

    assert exact_issues == []
    assert [(issue.code, issue.field) for issue in oversized_issues] == [
        ("invalid_mcf_metadata", "source_url")
    ]


def test_mcf_export_blocks_incomplete_review_without_file_or_state(tmp_path):
    db, review, _source = _review_with_filter(
        tmp_path, classified=False, duration=10
    )
    try:
        with pytest.raises(repository.IncompleteMcfReview) as error:
            repository.export_saved_review_mcf(db, review.id, 0)

        assert [issue.code for issue in error.value.preflight.issues] == [
            "missing_category",
            "missing_subcategory",
            "missing_severity",
        ]
        assert not (tmp_path / "movie.mcf").exists()
        assert db.query(MediaItemExportRow).count() == 0
    finally:
        db.close()


def test_preflight_and_exports_reject_replaced_physical_media(tmp_path):
    db, review, media_path = _exportable_review(tmp_path)
    try:
        media_path.write_bytes(b"replacement-physical-version")

        with pytest.raises(
            repository.ReviewMembershipError, match="physical media version"
        ):
            repository.preflight_saved_review_mcf(db, review.id)
        with pytest.raises(
            repository.ReviewMembershipError, match="physical media version"
        ):
            repository.export_saved_review_mcf(db, review.id, review.revision)
        with pytest.raises(
            repository.ReviewMembershipError, match="physical media version"
        ):
            repository.export_saved_review_edl(db, review.id, review.revision)

        assert not media_path.with_suffix(".mcf").exists()
        assert not media_path.with_suffix(".edl").exists()
        assert db.query(MediaItemExportRow).count() == 0
    finally:
        db.close()


def test_mcf_export_commit_failure_restores_mcf_and_leaves_edl_untouched(
    tmp_path, monkeypatch
):
    db, review, media_path = _exportable_review(tmp_path)
    try:
        for export_format in ExportFormat:
            db.add(
                MediaItemExportRow(
                    media_item_id="media",
                    media_source_id="",
                    format=export_format,
                    review_id=review.id,
                    exported_format_revision=0,
                    scans=[MediaItemExportScanRow(scan_id="scan", role="both")],
                )
            )
        db.commit()
        media_path.with_suffix(".mcf").write_bytes(b"previous mcf")
        media_path.with_suffix(".edl").write_bytes(b"previous edl")

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(ValueError, match="Unable to export MCF"):
            repository.export_saved_review_mcf(db, review.id, 0)

        assert media_path.with_suffix(".mcf").read_bytes() == b"previous mcf"
        assert media_path.with_suffix(".edl").read_bytes() == b"previous edl"
        assert db.query(MediaItemExportRow).count() == 2
        assert not list(tmp_path.glob(".movie.mcf.*.tmp"))
        assert not list(tmp_path.glob(".movie.mcf.*.bak"))
    finally:
        db.close()


def test_mcf_export_replace_failure_preserves_previous_file_and_state(
    tmp_path, monkeypatch
):
    db, review, media_path = _exportable_review(tmp_path)
    try:
        db.add(
            MediaItemExportRow(
                media_item_id="media",
                media_source_id="",
                format=ExportFormat.MCF,
                review_id=review.id,
                exported_format_revision=0,
                scans=[MediaItemExportScanRow(scan_id="scan", role="both")],
            )
        )
        db.commit()
        destination = media_path.with_suffix(".mcf")
        destination.write_bytes(b"previous mcf")
        real_replace = repository.os.replace

        def fail_replacement(source, target):
            if str(source).endswith(".tmp") and Path(target) == destination:
                raise OSError("injected replacement failure")
            return real_replace(source, target)

        monkeypatch.setattr(repository.os, "replace", fail_replacement)
        with pytest.raises(ValueError, match="Unable to export MCF"):
            repository.export_saved_review_mcf(db, review.id, 0)

        assert destination.read_bytes() == b"previous mcf"
        assert db.query(MediaItemExportRow).count() == 1
        assert not list(tmp_path.glob(".movie.mcf.*.tmp"))
        assert not list(tmp_path.glob(".movie.mcf.*.bak"))
    finally:
        db.close()


def test_mcf_export_restore_failure_preserves_recovery_backup(tmp_path, monkeypatch):
    db, review, media_path = _exportable_review(tmp_path)
    try:
        destination = media_path.with_suffix(".mcf")
        destination.write_bytes(b"known-good")
        real_replace = repository.os.replace

        def fail_backup_restore(source, target):
            if str(source).endswith(".bak"):
                raise OSError("injected restore failure")
            return real_replace(source, target)

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(repository.os, "replace", fail_backup_restore)
        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(ValueError, match="restore the previous MCF"):
            repository.export_saved_review_mcf(db, review.id, 0)

        backups = list(tmp_path.glob(".movie.mcf.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == b"known-good"
        assert db.query(MediaItemExportRow).count() == 0
        assert not list(tmp_path.glob(".movie.mcf.*.tmp"))
    finally:
        db.close()


def test_real_edl_and_mcf_exports_coexist_and_stale_independently(tmp_path):
    db, review, media_path = _exportable_review(tmp_path)
    try:
        repository.export_saved_review_edl(db, review.id, 0)
        repository.export_saved_review_mcf(db, review.id, 0)
        assert media_path.with_suffix(".edl").exists()
        assert media_path.with_suffix(".mcf").exists()

        changed_filter = review.filters[0].model_copy(
            update={"mcf_comment": "MCF changed"}
        )
        changed = repository.save_saved_review(
            db,
            review.id,
            repository.ReviewSave(
                revision=0,
                name=review.name,
                memberships=review.memberships,
                filters=[
                    repository.ReviewFilterInput(**changed_filter.model_dump()),
                    *[
                        repository.ReviewFilterInput(**item.model_dump())
                        for item in review.filters[1:]
                    ],
                ],
                mcf_document=review.mcf_document,
                synchronization=review.synchronization,
            ),
        )
        assert changed.exports.exports[ExportFormat.EDL].current is True
        assert changed.exports.exports[ExportFormat.MCF].outdated is True

        reexported = repository.export_saved_review_mcf(
            db, changed.id, changed.revision
        )
        assert reexported.exported_format_revision == 1
        assert reexported.exported_review.exports.exports[ExportFormat.MCF].current is True
        assert reexported.exported_review.exports.exports[ExportFormat.EDL].current is True

        label_changed = repository.save_saved_review(
            db,
            changed.id,
            repository.ReviewSave(
                revision=changed.revision,
                name=changed.name,
                memberships=changed.memberships,
                filters=[
                    repository.ReviewFilterInput(
                        **(
                            item.model_dump()
                            | ({"label": "EDL changed"} if index == 0 else {})
                        )
                    )
                    for index, item in enumerate(changed.filters)
                ],
                mcf_document=changed.mcf_document,
                synchronization=changed.synchronization,
            ),
        )
        assert label_changed.exports.exports[ExportFormat.EDL].outdated is True
        assert label_changed.exports.exports[ExportFormat.MCF].current is True
    finally:
        db.close()


def test_preflight_and_export_reject_unavailable_media_without_sidecar(tmp_path):
    db, review, media_path = _exportable_review(tmp_path)
    try:
        media_path.unlink()
        with pytest.raises(repository.ReviewMembershipError, match="unavailable"):
            repository.preflight_saved_review_mcf(db, review.id)
        with pytest.raises(repository.ReviewMembershipError, match="unavailable"):
            repository.export_saved_review_mcf(db, review.id, 0)
        assert not media_path.with_suffix(".mcf").exists()
        assert db.query(MediaItemExportRow).count() == 0
    finally:
        db.close()


def _review_with_filter(tmp_path, *, classified, duration):
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
        video_hash=repository.compute_video_md5(str(media_path)),
        duration_seconds=10,
        status="complete",
        scan_audio=True,
        scan_video=False,
    )
    source = ScanFindingRow(
        scan_id="scan",
        category=McfCategoryFamily.LANGUAGE if classified else None,
        subcategory="swearing" if classified else None,
        severity="medium" if classified else None,
        start_seconds=1,
        end_seconds=2,
        confidence=1,
        label="Evidence",
        source="audio",
        selected=True,
        edit_action="mute",
    )
    db.add_all([media, scan, source])
    db.commit()
    review = repository.create_saved_review(
        db,
        media_item_id="media",
        media_source_id=None,
        data=ReviewCreate(
            name="Review",
            memberships=[ReviewScanMembership(scan_id="scan", role="audio")],
        ),
    )
    if duration is not None:
        db.get(repository.SavedReviewRow, review.id).mcf_document.material_end_seconds = duration
        db.commit()
        review = repository.get_saved_review(db, review.id)
    return db, review, source


def _exportable_review(tmp_path):
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
        video_hash=repository.compute_video_md5(str(media_path)),
        duration_seconds=10,
        status="complete",
        scan_audio=True,
        scan_video=True,
    )
    findings = [
        ScanFindingRow(
            id=1,
            scan_id="scan",
            category=McfCategoryFamily.LANGUAGE,
            subcategory="swearing",
            severity=Severity.HIGH,
            mcf_channel=McfChannel.AUDIO,
            start_seconds=1,
            end_seconds=3,
            confidence=1,
            source="audio",
            selected=True,
            edit_action="mute",
        ),
        ScanFindingRow(
            id=2,
            scan_id="scan",
            category=McfCategoryFamily.VIOLENCE,
            subcategory="violence",
            severity=Severity.MEDIUM,
            mcf_channel=McfChannel.VIDEO,
            start_seconds=1,
            end_seconds=3,
            confidence=1,
            source="visual",
            selected=True,
            edit_action="skip",
        ),
        ScanFindingRow(
            id=3,
            scan_id="scan",
            category=McfCategoryFamily.NUDITY,
            subcategory="nudity",
            severity=Severity.LOW,
            mcf_channel=McfChannel.VIDEO,
            start_seconds=2,
            end_seconds=4,
            confidence=1,
            source="visual",
            selected=True,
            edit_action="skip",
        ),
    ]
    db.add_all([media, scan, *findings])
    db.commit()
    review = repository.create_saved_review(
        db,
        media_item_id="media",
        media_source_id=None,
        data=ReviewCreate(
            name="Review",
            memberships=[ReviewScanMembership(scan_id="scan", role="both")],
        ),
    )
    db.get(repository.SavedReviewRow, review.id).mcf_document.material_end_seconds = 10
    db.commit()
    review = repository.get_saved_review(db, review.id)
    return db, review, media_path
