import os

import pytest
from fastapi import HTTPException

from scanner import api, repository
from scanner.credits.credits_detector import ChapterMarker, detect_from_observations
from scanner.db import MediaVersionRow, ScanFindingRow, ScanQueueRow, ScanRow
from scanner.http import credits_api
from scanner.models import (
    AcceptedCreditsReference,
    CreditsAcceptanceRequest,
    CreditsDetectionStartRequest,
    ReviewCreate,
    ReviewFilterInput,
    ReviewMcfDocument,
    ReviewSave,
    ScanMode,
)
from scanner.persistence.repository.media_identity_store import scan_version_id
from tests.credits_api_support import _database, review_caller
from tests.jellyfin_item_support import jellyfin_target
from tests.scan_access_support import owner_caller


def test_start_credits_detection_queues_once_and_reconnects(monkeypatch, tmp_path):
    db = _database()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    request = CreditsDetectionStartRequest(
        target=jellyfin_target(monkeypatch, db, video_path),
        qwen_model=None,
    )

    started = api.api_start_credits_detection(request, owner_caller(db))
    reconnected = api.api_start_credits_detection(request, owner_caller(db))

    assert started.scan.scan_mode == ScanMode.CREDITS_DETECTION
    assert started.scan.status == "queued"
    assert started.scan.scan_audio is False
    assert started.scan.scan_video is True
    assert started.scan.credits_detection is not None
    assert started.scan.credits_detection.configuration.qwen_model is None
    assert started.model_consent is None
    assert reconnected.scan.id == started.scan.id
    assert reconnected.queue_id == started.queue_id
    assert db.query(ScanQueueRow).count() == 1
    initial_version = scan_version_id(db, started.scan.id)
    assert initial_version is not None

    video_path.write_bytes(b"replacement-physical-version")
    replacement = api.api_start_credits_detection(request, owner_caller(db))
    assert replacement.scan.id != started.scan.id
    assert replacement.queue_id != started.queue_id
    assert db.query(ScanQueueRow).count() == 2
    replacement_version = scan_version_id(db, replacement.scan.id)
    assert replacement_version is not None and replacement_version != initial_version
    db.close()


def test_credits_reuses_the_sampled_version_without_reusing_an_older_attempt(monkeypatch, tmp_path):
    db = _database()
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"unchanged test media")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    request = CreditsDetectionStartRequest(target=jellyfin_target(monkeypatch, db, media))

    initial = api.api_start_credits_detection(request, owner_caller(db))
    stats = media.stat()
    os.utime(media, ns=(stats.st_atime_ns, stats.st_mtime_ns + 3_000_000_000))
    current = api.api_start_credits_detection(request, owner_caller(db))
    assert current.queue_id != initial.queue_id
    assert scan_version_id(db, initial.scan.id) == scan_version_id(db, current.scan.id)
    assert db.query(MediaVersionRow).count() == 1
    assert api.api_start_credits_detection(request, owner_caller(db)).queue_id == current.queue_id
    db.close()


@pytest.mark.parametrize(
    ("operation", "existing", "remap"),
    [
        ("sampled_media_fingerprint", False, False),
        ("sampled_media_fingerprint", True, False),
        ("sampled_media_fingerprint", False, True),
        ("create_scan", False, False),
        ("find_active_credits_detection", True, False),
    ],
)
def test_credits_admission_rejects_file_replacement_or_remapping(
    monkeypatch, tmp_path, operation, existing, remap
):
    db = _database()
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"original test media")
    target = media
    if remap:
        target = tmp_path / "jellyfin.mkv"
        target.symlink_to(media)
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    request = CreditsDetectionStartRequest(target=jellyfin_target(monkeypatch, db, target))
    active = api.api_start_credits_detection(request, owner_caller(db)) if existing else None
    read_or_stage = getattr(credits_api, operation)

    def replace_then_admit(*args, **kwargs):
        result = read_or_stage(*args, **kwargs)
        replacement = tmp_path / "new-generation.mkv"
        replacement.write_bytes(b"replacement with different test media content")
        if remap:
            target.unlink()
            target.symlink_to(replacement)
        else:
            replacement.replace(media)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(credits_api, operation, replace_then_admit)
        with pytest.raises(HTTPException) as refused:
            api.api_start_credits_detection(request, owner_caller(db))

    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "credits_media_unavailable"
    assert db.query(ScanRow).count() == int(existing)
    assert db.query(ScanQueueRow).count() == int(existing)
    assert db.query(MediaVersionRow).count() == int(existing)
    current = api.api_start_credits_detection(request, owner_caller(db))
    if active is not None:
        assert current.queue_id != active.queue_id
        assert scan_version_id(db, current.scan.id) != scan_version_id(db, active.scan.id)
    db.close()


def test_credits_terminal_and_consent_states_use_frozen_safe_errors(monkeypatch, tmp_path):
    db = _database()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    target = jellyfin_target(monkeypatch, db, video_path)
    started = api.api_start_credits_detection(
        CreditsDetectionStartRequest(target=target, qwen_model=None), owner_caller(db)
    )
    request = CreditsAcceptanceRequest(
        context={"kind": "import", "target": target},
        accepted_boundary_seconds=90,
    )

    for status, message, expected_status, expected_code in (
        ("cancelled", None, 409, "credits_detection_cancelled"),
        ("failed", "Credits detection failed", 500, "credits_detection_failed"),
        (
            "failed",
            "Optional model acquisition failed",
            503,
            "credits_model_acquisition_failed",
        ),
        (
            "awaiting_model_consent",
            None,
            409,
            "credits_model_consent_required",
        ),
    ):
        repository.update_scan_status(db, started.scan.id, status, message)
        with pytest.raises(HTTPException) as acceptance_error:
            api.api_accept_credits_detection(started.scan.id, request, review_caller(db))
        assert acceptance_error.value.status_code == expected_status
        assert acceptance_error.value.detail["code"] == expected_code
        with pytest.raises(HTTPException) as clip_error:
            api.api_credits_detection_clip(started.scan.id, 90, review_caller(db))
        assert clip_error.value.status_code == expected_status
        assert clip_error.value.detail["code"] == expected_code

    from scanner.security.access.api_shapes import AccessErrorCode
    from scanner.security.access.decisions import AccessDenied

    with pytest.raises(AccessDenied) as missing:
        api.api_accept_credits_detection("missing", request, review_caller(db))
    assert missing.value.code == AccessErrorCode.NOT_FOUND
    db.close()


def test_edited_acceptance_preserves_detected_finding_and_saves_provenance(monkeypatch, tmp_path):
    db = _database()
    video_path = tmp_path / "movie.mkv"
    video_path.write_bytes(b"generated-test-video")
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    target = jellyfin_target(monkeypatch, db, video_path)
    started = api.api_start_credits_detection(
        CreditsDetectionStartRequest(target=target, qwen_model=None), owner_caller(db)
    )
    configuration = started.scan.credits_detection.configuration
    result = detect_from_observations(
        duration_seconds=120.0,
        configuration=configuration,
        chapters=[ChapterMarker(90.0, "End Credits")],
    )
    repository.complete_credits_detection(
        db, scan_id=started.scan.id, cache_key="a" * 64, result=result
    )
    preview_call = {}

    def render_preview(path, start_seconds, end_seconds):
        preview_call.update(
            path=path,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        return b"mp4"

    monkeypatch.setattr(repository, "render_video_preview", render_preview)
    preview = api.api_credits_detection_clip(started.scan.id, 92.0, review_caller(db))
    assert preview.body == b"mp4"
    assert preview.media_type == "video/mp4"
    assert preview.headers["content-disposition"] == ('inline; filename="credits-preview.mp4"')
    assert preview.headers["cache-control"] == "no-store"
    assert preview_call == {
        "path": video_path,
        "start_seconds": 86.0,
        "end_seconds": 98.0,
    }
    media = db.get(repository.MediaItemRow, started.scan.media_item_id)
    review = repository.create_saved_review(
        db,
        media_item_id=media.id,
        media_source_id=None,
        duration_seconds=120.0,
        data=ReviewCreate(name="Review"),
        catalog_mcf_document=ReviewMcfDocument(
            title="Movie", material_start_seconds=0, material_end_seconds=90
        ),
        target_source="jellyfin",
        target_item_id=media.jellyfin_id,
    )

    acceptance = api.api_accept_credits_detection(
        started.scan.id,
        CreditsAcceptanceRequest(
            context={"kind": "review", "review_id": review.id, "revision": 0},
            accepted_boundary_seconds=92.0,
        ),
        review_caller(db),
    )
    saved = repository.save_saved_review(
        db,
        review.id,
        ReviewSave(
            revision=review.revision,
            name=review.name,
            memberships=[acceptance.membership],
            filters=[acceptance.filter],
            mcf_document=review.mcf_document,
            synchronization=review.synchronization,
            accepted_credits=acceptance.accepted_credits,
        ),
        target_source="jellyfin",
        target_item_id=media.jellyfin_id,
    )

    source = db.query(ScanFindingRow).filter_by(scan_id=started.scan.id).one()
    assert source.start_seconds == 90.0
    assert acceptance.filter.start_seconds == 92.0
    assert saved.accepted_credits.detected_boundary_seconds == 90.0
    assert saved.accepted_credits.accepted_boundary_seconds == 92.0
    assert saved.filters[0].start_seconds == 92.0

    with pytest.raises(repository.ReviewMembershipError, match="candidate"):
        repository.save_saved_review(
            db,
            saved.id,
            ReviewSave(
                revision=saved.revision,
                name=saved.name,
                memberships=saved.memberships,
                filters=[ReviewFilterInput(**value.model_dump()) for value in saved.filters],
                mcf_document=saved.mcf_document,
                synchronization=saved.synchronization,
                accepted_credits=AcceptedCreditsReference(
                    scan_id=started.scan.id,
                    candidate_id="forged-candidate",
                    accepted_boundary_seconds=92,
                ),
            ),
        )

    with pytest.raises(HTTPException) as invalid_boundary:
        api.api_credits_detection_clip(started.scan.id, 120.0, review_caller(db))
    assert invalid_boundary.value.status_code == 422
    assert invalid_boundary.value.detail["code"] == "invalid_credits_boundary"

    monkeypatch.setattr(
        repository,
        "render_video_preview",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("/private/media/movie.mkv")),
    )
    with pytest.raises(HTTPException) as render_failure:
        api.api_credits_detection_clip(started.scan.id, 92.0, review_caller(db))
    assert render_failure.value.status_code == 500
    assert render_failure.value.detail["code"] == "credits_clip_failed"
    assert "/private/media/movie.mkv" not in str(render_failure.value.detail)

    monkeypatch.setattr(
        repository, "render_video_preview", lambda *_args: b"x" * (16 * 1024 * 1024 + 1)
    )
    with pytest.raises(HTTPException) as oversized:
        api.api_credits_detection_clip(started.scan.id, 92.0, review_caller(db))
    assert oversized.value.status_code == 413
    assert oversized.value.detail["code"] == "credits_clip_too_large"

    video_path.write_bytes(b"different-physical-version")
    with pytest.raises(HTTPException) as status_mismatch:
        api.api_get_credits_detection(started.scan.id, owner_caller(db))
    assert status_mismatch.value.status_code == 409
    assert status_mismatch.value.detail["code"] == "credits_target_mismatch"
    with pytest.raises(HTTPException) as clip_mismatch:
        api.api_credits_detection_clip(started.scan.id, 92.0, review_caller(db))
    assert clip_mismatch.value.status_code == 409
    assert clip_mismatch.value.detail["code"] == "credits_target_mismatch"
    with pytest.raises(HTTPException) as acceptance_mismatch:
        api.api_accept_credits_detection(
            started.scan.id,
            CreditsAcceptanceRequest(
                context={
                    "kind": "review",
                    "review_id": review.id,
                    "revision": saved.revision,
                },
                accepted_boundary_seconds=92.0,
            ),
            review_caller(db),
        )
    assert acceptance_mismatch.value.status_code == 409
    assert acceptance_mismatch.value.detail["code"] == "credits_target_mismatch"

    video_path.unlink()
    with pytest.raises(HTTPException) as status_unavailable:
        api.api_get_credits_detection(started.scan.id, owner_caller(db))
    assert status_unavailable.value.status_code == 503
    assert status_unavailable.value.detail["code"] == "credits_media_unavailable"
    with pytest.raises(HTTPException) as unavailable:
        api.api_credits_detection_clip(started.scan.id, 92.0, review_caller(db))
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail["code"] == "credits_media_unavailable"
    db.close()
