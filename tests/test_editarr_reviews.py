from pathlib import Path

import pytest

from scanner import repository
from scanner.models import (
    EditAction,
    ExportFormat,
    FilterDefaultsUpdate,
    FilterRuleDisposition,
    FilterSelectionRule,
    FindingCreate,
    McfCategoryFamily,
    ReviewCreate,
    ReviewScanMembership,
)
from scanner.pipeline import runner
from tests.editarr_support import db as db


def test_new_scan_does_not_apply_review_time_audio_defaults(
    db, tmp_path: Path, monkeypatch
):
    video_path = tmp_path / "example.mkv"
    video_path.write_bytes(b"video")
    cached_audio = tmp_path / "cache" / "videos" / "hash123" / "audio.wav"
    cached_audio.parent.mkdir(parents=True, exist_ok=True)
    cached_audio.write_bytes(b"wav")

    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path=str(video_path),
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(video_path),
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        frame_interval=None,
        speed_up=False,
    )

    current_defaults = repository.get_structured_filter_defaults_settings(db)
    repository.update_filter_defaults_settings(
        db,
        FilterDefaultsUpdate(
            revision=current_defaults.revision,
            selection_rules=[
                FilterSelectionRule(
                    id="031a4a17-1d0c-4ce0-a686-797ac92fb2d6",
                    disposition=FilterRuleDisposition.ALLOW,
                    evidence_source="audio",
                    label_contains="dang",
                ),
                FilterSelectionRule(
                    id="cc6e80ad-93aa-44ac-8292-9df6d62cc767",
                    disposition=FilterRuleDisposition.ALLOW,
                    evidence_source="audio",
                    label_contains="hell",
                ),
                FilterSelectionRule(
                    id="a0f7e854-5ca6-4695-9076-440952780a76",
                    disposition=FilterRuleDisposition.DENY,
                    evidence_source="audio",
                    label_contains="hell",
                ),
            ],
            severity_defaults=current_defaults.severity_defaults,
        ),
    )

    monkeypatch.setattr(runner.settings, "frames_dir", tmp_path / "frames")
    monkeypatch.setattr(runner, "cached_audio_path", lambda _hash: cached_audio)
    monkeypatch.setattr(
        runner,
        "transcription_cache_path",
        lambda _video_hash, _model_name: (
            tmp_path / "cache" / "videos" / "hash123" / "transcript_base.json"
        ),
    )
    monkeypatch.setattr(
        runner,
        "extract_audio",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("extract_audio should be skipped when cached audio exists")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_scan_audio_isolated",
        lambda *_args, **_kwargs: [
            FindingCreate(
                video_id=scan.id,
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=1.0,
                end_seconds=1.3,
                confidence=0.9,
                label="dang",
                source="audio",
                selected=True,
            ),
            FindingCreate(
                video_id=scan.id,
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=2.0,
                end_seconds=2.3,
                confidence=0.9,
                label="hell",
                source="audio",
                selected=True,
            ),
        ],
    )

    runner.run_scan(
        db,
        str(video_path),
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_id=scan.id,
        video_duration_seconds=30.0,
        status_updater=repository.update_scan_status,
        findings_writer=repository.create_scan_findings_batch,
        scan_metadata_updater=repository.update_scan_duration,
    )

    saved = repository.get_scan_findings(db, scan.id)
    assert saved is not None
    by_label = {finding.label: finding.selected for finding in saved.findings}
    assert by_label["dang"] is True
    assert by_label["hell"] is True


def test_review_edl_export_is_normalized_atomic_and_records_only_success(
    db, tmp_path: Path, monkeypatch
):
    source = tmp_path / "example.mkv"
    source.write_bytes(b"video")
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path="/remote/example.mkv",
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(source),
        media_source_id="source-4k",
        media_source_name="4K HDR",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=True,
        video_model="nudenet",
        frame_interval=1,
        speed_up=False,
    )
    repository.update_scan_status(db, scan.id, "complete")
    repository.create_scan_findings_batch(
        db,
        scan.id,
        [
            FindingCreate(
                video_id="unused",
                category=McfCategoryFamily.NUDITY,
                subcategory="nudity",
                start_seconds=10,
                end_seconds=20,
                confidence=0.9,
                source="visual",
                edit_action=EditAction.SKIP,
            ),
            FindingCreate(
                video_id="unused",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=15,
                end_seconds=25,
                confidence=0.8,
                source="audio",
                edit_action=EditAction.MUTE,
            ),
        ],
    )
    assert repository.list_scan_history(db)[0].finding_count == 2
    media_item.path = str(source)
    db.commit()

    review = repository.create_saved_review(
        db,
        media_item_id=media_item.id,
        media_source_id="source-4k",
        duration_seconds=100,
        data=ReviewCreate(
            name="Combined review",
            memberships=[ReviewScanMembership(scan_id=scan.id, role="both")],
        ),
    )
    exported = repository.export_saved_review_edl(db, review.id, review.revision)
    assert exported is not None
    assert exported.review_id == review.id
    assert [(ref.id, ref.scan_id, ref.role) for ref in exported.exported_scans] == [
        (scan.id, 1, "both")
    ]
    edl_path = source.with_suffix(".edl")
    assert edl_path.read_text() == (
        "## Editarr Simple EDL\n"
        "## nudity: Imported skip\n10.000 20.000 3\n"
        "## swearing: Imported mute\n20.000 25.000 1"
    )
    item_status = repository.library_item_export_statuses(
        db, server_id="http://jellyfin.test", jellyfin_item_ids=["movie-1"]
    )["movie-1"].exports[ExportFormat.EDL]
    assert item_status.present is True
    assert [(ref.id, ref.scan_id, ref.role) for ref in item_status.exported_scans] == [
        (scan.id, 1, "both")
    ]
    version_status = repository.library_item_version_export_statuses(
        db, server_id="http://jellyfin.test", jellyfin_item_id="movie-1"
    )["source-4k"].exports[ExportFormat.EDL]
    assert version_status.present is True
    assert [(ref.id, ref.scan_id, ref.role) for ref in version_status.exported_scans] == [
        (scan.id, 1, "both")
    ]

    preview_calls: list[tuple[str, float, float]] = []
    monkeypatch.setattr(
        repository,
        "render_mute_preview",
        lambda path, start, end: (preview_calls.append((str(path), start, end)) or b"MP3"),
    )
    current_findings = review.filters
    audio_finding = next(
        finding for finding in current_findings if finding.source == "audio"
    )
    assert repository.render_saved_review_filter_preview(
        db, review.id, audio_finding.id, preview_kind="mute"
    ) == b"MP3"
    # The preview uses only this filter's raw saved Whisper interval, without
    # decoding or concatenating the rest of the feature-length media.
    assert preview_calls == [(str(source), 15.0, 25.0)]

    video_preview_calls: list[tuple[str, float, float]] = []
    monkeypatch.setattr(
        repository,
        "render_video_preview",
        lambda path, start, end: (video_preview_calls.append((str(path), start, end)) or b"MP4"),
    )
    video_finding = next(
        finding for finding in current_findings if finding.source == "visual"
    )
    assert repository.render_saved_review_filter_preview(
        db, review.id, video_finding.id, preview_kind="video"
    ) == b"MP4"
    assert video_preview_calls == [(str(source), 10.0, 20.0)]

    initial_content = edl_path.read_text()

    def fail_replace(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(repository.os, "replace", fail_replace)
    with pytest.raises(ValueError, match="Unable to export EDL"):
        repository.export_saved_review_edl(db, review.id, review.revision)
    assert edl_path.read_text() == initial_content
    assert not list(tmp_path.glob(".example.edl.*.tmp"))


def test_edl_export_combines_review_audio_and_video_memberships(db, tmp_path: Path):
    source = tmp_path / "example.mkv"
    source.touch()
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path=str(source),
    )
    audio_scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(source),
        media_source_id="source-4k",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        frame_interval=None,
        speed_up=False,
    )
    video_scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(source),
        media_source_id="source-4k",
        scan_audio=False,
        audio_model=None,
        audio_filter=None,
        scan_video=True,
        video_model="nudenet",
        frame_interval=1,
        speed_up=False,
    )
    second_audio_scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(source),
        media_source_id="source-4k",
        scan_audio=True,
        audio_model="mlx_whisper",
        audio_filter="glin_profanity",
        scan_video=False,
        video_model=None,
        frame_interval=None,
        speed_up=False,
    )
    for scan in (audio_scan, second_audio_scan, video_scan):
        repository.update_scan_status(db, scan.id, "complete")
    repository.create_scan_findings_batch(
        db,
        audio_scan.id,
        [
            FindingCreate(
                video_id="unused",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=20,
                end_seconds=25,
                confidence=0.8,
                source="audio",
                edit_action=EditAction.MUTE,
            ),
            FindingCreate(
                video_id="unused",
                category=McfCategoryFamily.NUDITY,
                subcategory="nudity",
                start_seconds=1,
                end_seconds=2,
                confidence=0.9,
                source="visual",
                edit_action=EditAction.SKIP,
            ),
        ],
    )
    repository.create_scan_findings_batch(
        db,
        video_scan.id,
        [
            FindingCreate(
                video_id="unused",
                category=McfCategoryFamily.NUDITY,
                subcategory="nudity",
                start_seconds=10,
                end_seconds=18,
                confidence=0.9,
                source="visual",
                edit_action=EditAction.SKIP,
            ),
            FindingCreate(
                video_id="unused",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=3,
                end_seconds=4,
                confidence=0.8,
                source="audio",
                edit_action=EditAction.MUTE,
            ),
        ],
    )
    repository.create_scan_findings_batch(
        db,
        second_audio_scan.id,
        [FindingCreate(
            video_id="unused",
            category=McfCategoryFamily.LANGUAGE,
            subcategory="swearing",
            start_seconds=30,
            end_seconds=35,
            confidence=0.8,
            source="audio",
            edit_action=EditAction.MUTE,
        )],
    )

    review = repository.create_saved_review(
        db,
        media_item_id=media_item.id,
        media_source_id="source-4k",
        duration_seconds=100,
        data=ReviewCreate(
            name="Multi-scan",
            memberships=[
                ReviewScanMembership(scan_id=audio_scan.id, role="audio"),
                ReviewScanMembership(scan_id=video_scan.id, role="video"),
                ReviewScanMembership(scan_id=second_audio_scan.id, role="audio"),
            ],
        ),
    )
    exported = repository.export_saved_review_edl(db, review.id, review.revision)

    assert exported is not None
    assert exported.review_id == review.id
    assert [(ref.id, ref.scan_id, ref.role) for ref in exported.exported_scans] == [
        (audio_scan.id, 1, "audio"),
        (video_scan.id, 2, "video"),
        (second_audio_scan.id, 3, "audio"),
    ]
    assert source.with_suffix(".edl").read_text() == (
        "## Editarr Simple EDL\n"
        "## nudity: Imported skip\n10.000 18.000 3\n"
        "## swearing: Imported mute\n20.000 25.000 1\n"
        "## swearing: Imported mute\n30.000 35.000 1"
    )
    for contributing_scan in (audio_scan, video_scan, second_audio_scan):
        with pytest.raises(ValueError, match="active EDL"):
            repository.delete_scan(db, contributing_scan.id)


def test_deleting_one_media_version_edl_preserves_other_versions(db, tmp_path: Path):
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path="/remote/example.mkv",
    )
    scans = []
    for source_id in ("source-4k", "source-1080p"):
        source = tmp_path / f"{source_id}.mkv"
        source.touch()
        scan = repository.create_scan(
            db,
            media_item=media_item,
            resolved_local_path=str(source),
            media_source_id=source_id,
            scan_audio=True,
            audio_model="whisper",
            audio_filter="better_profanity",
            scan_video=False,
            video_model=None,
            frame_interval=None,
            speed_up=False,
        )
        repository.update_scan_status(db, scan.id, "complete")
        media_item.path = str(source)
        db.commit()
        review = repository.create_saved_review(
            db,
            media_item_id=media_item.id,
            media_source_id=source_id,
            duration_seconds=100,
            data=ReviewCreate(
                name=f"Review {source_id}",
                memberships=[ReviewScanMembership(scan_id=scan.id, role="audio")],
            ),
        )
        repository.export_saved_review_edl(db, review.id, review.revision)
        scans.append((scan, source))

    assert repository.delete_library_item_export(
        db,
        server_id="http://jellyfin.test",
        jellyfin_item_id="movie-1",
        media_source_id="source-4k",
        export_format=ExportFormat.EDL,
    ) is True

    statuses = repository.library_item_version_export_statuses(
        db,
        server_id="http://jellyfin.test",
        jellyfin_item_id="movie-1",
    )
    assert "source-4k" not in statuses
    assert statuses["source-1080p"].exports[ExportFormat.EDL].present is True
    assert not scans[0][1].with_suffix(".edl").exists()
    assert scans[1][1].with_suffix(".edl").exists()
