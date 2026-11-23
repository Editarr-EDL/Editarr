"""Saved review edits and previews verify strong media pins outside the writer lock."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from scanner import repository
from scanner.db import SavedReviewRow, ScanRow
from scanner.persistence.repository import review_access, review_version_evidence
from scanner.persistence.repository.media_identity_store import bind_review_version, observe_version
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import app as app
from tests.test_review_policy_binding import body, import_review
from tests.test_review_policy_roundoff import retime


@pytest.fixture
def pinned_review(app, monkeypatch):
    item_id = app.jellyfin_item(guid("saved-review-media-proof"), LIBRARY_A)
    detail = import_review(app, monkeypatch, item_id, b"1.000 2.000 1\n3.000 4.000 3\n")
    review_id = detail["id"]
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review_id)
        scan = row.memberships[0].scan
        path = Path(scan.resolved_local_path)
        version = observe_version(
            db,
            media=row.media_item,
            media_source_id=scan.media_source_id,
            source_path=scan.source_path,
            resolved_path=str(path),
            content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            scan_id=scan.id,
        )
        bind_review_version(db, review_id, version)
        db.commit()
    monkeypatch.setattr(repository, "render_mute_preview", lambda *args, **kwargs: b"audio")
    monkeypatch.setattr(repository, "render_video_preview", lambda *args, **kwargs: b"video")
    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 60.0)
    return item_id, detail, path


def _request(app, pinned_review, operation, *, target=None):
    item_id, detail, _ = pinned_review
    review_id = detail["id"]
    headers = app.accounts.unsafe_cookie(USER)
    path = f"/api/reviews/{review_id}"
    if operation in {"save", "save-manual"}:
        draft = body(detail) | {"name": "Edited name"}
        if operation == "save-manual":
            draft["new_manual_filters"] = [
                {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "source": "visual",
                    "category": "violence",
                    "subcategory": "violence",
                    "start_seconds": 6,
                    "end_seconds": 7,
                    "edit_action": "skip",
                    "label": "New cue",
                    "details": None,
                    "group_key": None,
                }
            ]
        return app.send("PUT", path, headers, draft)
    if operation == "retime":
        return retime(app, detail, (0, 60))
    if operation == "draft-preview":
        cue = detail["filters"][0]
        fields = (
            "source_scan_id",
            "source_finding_id",
            "source",
            "edit_action",
            "start_seconds",
            "end_seconds",
        )
        return app.send(
            "POST",
            f"/api/library-items/{target or item_id}/reviews/preview",
            headers,
            {"review_id": review_id, **{key: cue[key] for key in fields}},
        )
    channel = "audio" if operation == "mute-preview" else "visual"
    cue = next(value for value in detail["filters"] if value["source"] == channel)
    return app.send("GET", f"{path}/filters/{cue['id']}/{operation}", headers)


@pytest.mark.parametrize(
    "operation", ["save", "save-manual", "retime", "mute-preview", "video-preview", "draft-preview"]
)
def test_saved_review_hashes_once_before_sqlite_writer_lock(
    app, monkeypatch, pinned_review, operation
):
    observed = []

    def fingerprint(path):
        with closing(sqlite3.connect(app.accounts.path, timeout=0.03)) as writer:
            writer.execute("BEGIN IMMEDIATE")
        observed.append(Path(path))
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    monkeypatch.setattr(review_version_evidence, "content_digest", fingerprint)
    if operation == "draft-preview":
        from scanner.http.review_targets import ReviewTarget

        def unexpected_store(*args, **kwargs):
            pytest.fail("Saved draft preview persisted its target mapping")

        monkeypatch.setattr(ReviewTarget, "store", unexpected_store)

    response = _request(app, pinned_review, operation)
    assert response.status_code == 200, response.text
    assert observed == [pinned_review[2]]
    if operation == "save-manual":
        assert any(value["label"] == "New cue" for value in response.json()["filters"])


@pytest.mark.parametrize(
    "operation", ["save", "save-manual", "retime", "mute-preview", "draft-preview"]
)
def test_saved_review_rechecks_its_file_generation_at_write_admission(
    app, monkeypatch, pinned_review, operation
):
    _, detail, media = pinned_review
    capture = review_access.capture_review_media_proof
    reads = []

    def fingerprint(path):
        reads.append(Path(path))
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def capture_then_replace(*args):
        proof = capture(*args)
        replacement = media.with_suffix(".replacement")
        replacement.write_bytes(b"Changed physical version after proof capture")
        replacement.replace(media)
        return proof

    monkeypatch.setattr(review_version_evidence, "content_digest", fingerprint)
    monkeypatch.setattr(review_access, "capture_review_media_proof", capture_then_replace)
    response = _request(app, pinned_review, operation)
    assert response.status_code == (404 if operation == "draft-preview" else 409), response.text
    assert reads == [media]
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, detail["id"])
        assert (row.name, row.revision) == (detail["name"], detail["revision"])
        assert not db.query(ScanRow).filter_by(origin="manual").count()


@pytest.mark.parametrize("operation", ["save", "draft-preview"])
def test_saved_review_rejects_unapproved_changes_before_reading_media(
    app, monkeypatch, pinned_review, operation
):
    def unexpected_fingerprint(_path):
        pytest.fail("Media was read before the target and action were authorized")

    monkeypatch.setattr(review_version_evidence, "content_digest", unexpected_fingerprint)
    target = None
    if operation == "save":
        app.accounts.set_override(USER, C.REVIEWS_RENAME_OWN, OverrideEffect.DENY)
    else:
        target = app.jellyfin_item(guid("mismatched-preview-item"), LIBRARY_A)
    response = _request(app, pinned_review, operation, target=target)
    assert response.status_code == (403 if operation == "save" else 404), response.text
