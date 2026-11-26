"""Real review admission and filesystem sidecar publication for subtitles."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import scanner.api  # noqa: F401 - initialize the API before importing its routes
from scanner.db import SavedReviewRow, ScanRow
from scanner.http import review_subtitle_api
from scanner.persistence.repository import review_version_evidence
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import PEER_USER, USER
from tests.subtitle_review_http_support import (
    SUBTITLE_TEXT,
    pin_review_file,
    preview_for_publication,
    seed_subtitle_review,
    selected_source,
    subtitle_request,
)
from tests.subtitle_review_http_support import app as app
from tests.subtitle_review_http_support import (
    subtitle_publishing_dependencies as subtitle_publishing_dependencies,
)


@pytest.mark.parametrize("endpoint", ["sources", "preview", "publish"])
def test_subtitle_fingerprint_leaves_other_database_writers_unblocked(app, monkeypatch, endpoint):
    app.client.app.include_router(review_subtitle_api.router)
    monkeypatch.setattr(review_subtitle_api, "_refresh", lambda *_: True)
    review, _source = seed_subtitle_review(app)
    media = pin_review_file(app, review)
    if endpoint == "publish":
        payload = preview_for_publication(app, review)
    elif endpoint == "preview":
        payload = selected_source(app, review)
    else:
        payload = {}
    hashed = []

    def fingerprint(path):
        with closing(sqlite3.connect(app.accounts.path, timeout=0.1)) as other:
            other.execute("BEGIN IMMEDIATE")
            hashed.append(Path(path))
        return sampled_media_fingerprint(path)

    monkeypatch.setattr(review_version_evidence, "sampled_media_fingerprint", fingerprint)
    response = subtitle_request(app, review, endpoint, payload)
    assert response.status_code == 200, response.text
    assert hashed and set(hashed) == {media}


def test_subtitle_publish_refuses_replacement_after_fingerprint(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    review, source = seed_subtitle_review(app)
    media = pin_review_file(app, review)
    payload = preview_for_publication(app, review)
    names = {path.name for path in media.parent.iterdir()}
    current_fingerprint = review_version_evidence.ReviewMediaProof.current_fingerprint
    replaced = False

    def replace_after_capture(proof, path):
        nonlocal replaced
        checksum = current_fingerprint(proof, path)
        if checksum is not None and not replaced:
            media.write_bytes(b"replacement after fingerprint capture")
            replaced = True
        return checksum

    monkeypatch.setattr(
        review_version_evidence.ReviewMediaProof, "current_fingerprint", replace_after_capture
    )
    response = subtitle_request(app, review, "publish", payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "subtitle_media_changed"
    assert replaced
    assert {path.name for path in media.parent.iterdir()} == names
    assert source.read_bytes() == SUBTITLE_TEXT


def test_subtitle_export_denial_does_not_fingerprint_media(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    review, _source = seed_subtitle_review(app)
    pin_review_file(app, review)
    for capability in (
        C.EXPORTS_PERSONAL_DOWNLOAD_OWN,
        C.EXPORTS_PERSONAL_PUBLISH_OWN,
        C.EXPORTS_DEFAULT_PUBLISH,
    ):
        app.accounts.set_override(USER, capability, OverrideEffect.DENY)
    monkeypatch.setattr(
        review_version_evidence,
        "sampled_media_fingerprint",
        lambda _path: pytest.fail("An unauthorized subtitle request tried to hash the media"),
    )
    denied = subtitle_request(app, review, "sources", {})
    assert denied.status_code in {403, 404}, denied.text


def test_exact_version_forced_preview_and_safe_repeat_publication(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    monkeypatch.setattr(review_subtitle_api, "_refresh", lambda *_: True)
    review, source = seed_subtitle_review(app)
    response = subtitle_request(app, review, "sources", {})
    assert response.status_code == 200, response.text
    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["label"] == "English forced"
    assert sources[0]["forced"] is True
    assert str(source.parent) not in response.text

    selected = {"source_id": sources[0]["id"]}
    preview = subtitle_request(app, review, "preview", selected).json()
    assert preview["changed_cues"] == 1
    assert preview["cues"][0]["filtered"] == "close the gate."
    payload = selected | {"preview_token": preview["preview_token"]}

    source.write_bytes(SUBTITLE_TEXT + b"\n")
    changed = subtitle_request(app, review, "publish", payload)
    assert changed.status_code == 409, changed.text
    source.write_bytes(SUBTITLE_TEXT)

    first = subtitle_request(app, review, "publish", payload)
    assert first.status_code == 200, first.text
    published = source.parent / first.json()["filename"]
    assert published.name.endswith(".en.forced.srt")
    assert not first.json()["replaced"]
    assert published.read_bytes() == SUBTITLE_TEXT.replace(b"Damn, ", b"")
    assert source.read_bytes() == SUBTITLE_TEXT
    repeated = subtitle_request(app, review, "publish", payload)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["filename"] == first.json()["filename"]
    assert repeated.json()["replaced"]
    published.write_bytes(b"replacement from another tool")
    failed = subtitle_request(app, review, "publish", payload)
    assert failed.status_code == 409, failed.text
    assert published.read_bytes() == b"replacement from another tool"


def test_hidden_review_and_publish_only_role(app):
    app.client.app.include_router(review_subtitle_api.router)
    review, source = seed_subtitle_review(app)
    hidden = subtitle_request(app, review, "sources", {}, user=PEER_USER)
    assert hidden.status_code == 404, hidden.text

    app.accounts.set_override(USER, C.EXPORTS_PERSONAL_DOWNLOAD_OWN, OverrideEffect.DENY)
    response = subtitle_request(app, review, "sources", {})
    assert response.status_code == 200, response.text
    source.unlink()
    unavailable = subtitle_request(app, review, "sources", {})
    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json()["fallback"] == "scan_required"


def test_download_only_review_export_access_can_publish_shared_subtitles(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    monkeypatch.setattr(review_subtitle_api, "_refresh", lambda *_: True)
    review, source = seed_subtitle_review(app)
    for capability in (C.EXPORTS_PERSONAL_PUBLISH_OWN, C.EXPORTS_DEFAULT_PUBLISH):
        app.accounts.set_override(USER, capability, OverrideEffect.DENY)

    listed = subtitle_request(app, review, "sources", {})
    assert listed.status_code == 200, listed.text
    selected = {"source_id": listed.json()["sources"][0]["id"]}
    preview = subtitle_request(app, review, "preview", selected)
    assert preview.status_code == 200, preview.text
    published = subtitle_request(
        app, review, "publish", selected | {"preview_token": preview.json()["preview_token"]}
    )
    assert published.status_code == 200, published.text
    assert (source.parent / published.json()["filename"]).read_bytes() == SUBTITLE_TEXT.replace(
        b"Damn, ", b""
    )


def test_existing_subtitle_accepts_a_saved_review_without_scan_membership(app):
    app.client.app.include_router(review_subtitle_api.router)
    review, _source = seed_subtitle_review(app)
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        row.memberships.clear()
        db.commit()
    response = subtitle_request(app, review, "sources", {})
    assert response.status_code == 200, response.text
    assert response.json()["sources"][0]["supported"]


def test_malformed_external_subtitle_admits_track_bound_transcript_fallback(app, monkeypatch):
    from scanner.modeling.model_audio_tracks import AudioSelection, AudioTrackMetadata
    from scanner.persistence.repository import review_subtitles

    app.client.app.include_router(review_subtitle_api.router)
    review, source = seed_subtitle_review(app)
    source.write_text("No timed cues")
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        scan = db.get(ScanRow, row.memberships[0].scan_id)
        scan.analyzed_audio_selection_json = AudioSelection(
            file_version="a" * 64,
            track=AudioTrackMetadata(stream_index=0, language="eng", disposition=0),
        ).model_dump_json()
        db.commit()

    monkeypatch.setattr(
        review_subtitles,
        "cached_segments",
        lambda audio: (
            [{"start_seconds": 1, "end_seconds": 3, "text": "Damn, close the gate."}]
            if audio.selection.track.stream_index == 0
            else None
        ),
    )
    response = subtitle_request(app, review, "sources", {})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["fallback"] == "available"
    assert result["audio_sources"][0]["language"] == "en"
    assert result["audio_source_id"] == result["audio_sources"][0]["id"]
    assert {track["kind"]: track["supported"] for track in result["sources"]} == {
        "external": False,
        "transcript": True,
    }
    for field in ("audio_sources", "sources"):
        assert str(source.parent) not in repr(result[field])
