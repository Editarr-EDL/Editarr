"""An authorized sampled version can use its new path without rewriting scan provenance."""

from dataclasses import replace
from pathlib import Path

import pytest

from scanner import api, repository
from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.persistence.repository import media_identity_store as identity
from scanner.persistence.repository import repository_review_operations
from scanner.pipeline.audio_tracks import AudioTrack, AudioTrackSelection
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import review_app as review_app
from tests.test_review_policy_binding import body, import_review


@pytest.fixture
def moved_review(app, monkeypatch):
    item_id = app.jellyfin_item(guid("renamed-source"), LIBRARY_A)
    imported = import_review(
        app,
        monkeypatch,
        item_id,
        b"## swearing: Cut\n1.000 2.000 1\n## violence: Scene\n3.000 4.000 3\n",
    )
    headers = app.accounts.unsafe_cookie(USER)
    route = f"/api/reviews/{imported['id']}"
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, imported["id"])
        scan = row.memberships[0].scan
        original = Path(scan.resolved_local_path)
        for review_filter in row.filters:
            review_filter.severity = "low"
        row.mcf_document.material_end_seconds = 60
        version = identity.observe_sampled_version(
            db,
            media=row.media_item,
            media_source_id=scan.media_source_id,
            source_path=scan.source_path,
            fingerprint=sampled_media_fingerprint(original),
        )
        identity.bind_scan_version(db, scan.id, version)
        identity.bind_review_version(db, row.id, version)
        db.commit()
    detailed = app.send("GET", route, app.accounts.cookie(USER))
    assert detailed.status_code == 200, detailed.text
    assert detailed.json()["filters"][0]["selected"]
    moved = original.rename(original.with_suffix(".moved.mkv"))
    item = app.items[item_id]
    app.items[item_id] = item.model_copy(update={"path": str(moved)})
    with api.LIBRARY_ITEMS_CACHE_LOCK:
        api.JELLYFIN_ITEM_CACHE.clear()
    return route, detailed.json(), headers, original, moved


def test_renamed_media_allows_review_save_without_rewriting_scan_path(app, moved_review):
    route, review, headers, original, _ = moved_review
    response = app.send("PUT", route, headers, body(review) | {"name": "Retained scan"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Retained scan"
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review["id"])
        assert Path(row.memberships[0].scan.resolved_local_path) == original


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_renamed_media_allows_export_and_new_sidecar_at_current_path(app, moved_review, format):
    route, review, headers, original, moved = moved_review
    result = app.send(
        "POST", f"{route}/exports/{format}/download", headers, {"revision": review["revision"]}
    )
    assert result.status_code == 200, result.text
    assert (b"1.000 2.000 1" if format == "edl" else b"swearing=low=audio") in result.content
    published = app.send(
        "POST", f"{route}/exports/{format}", headers, {"revision": review["revision"]}
    )
    assert published.status_code == 200, published.text
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, published.json()["id"])
        destination = Path(row.destination_path)
        assert destination.parent.is_relative_to(moved.parent)
        assert destination.name.startswith(moved.stem + ".")
        assert destination.read_bytes() == result.content
        assert Path(row.review.memberships[0].scan.resolved_local_path) == original


def test_renamed_saved_preview_uses_authorized_current_path(app, moved_review, monkeypatch):
    route, review, headers, _, moved = moved_review
    rendered = []

    def render(path, *_args, **_kwargs):
        rendered.append(Path(path))
        return b"audio"

    monkeypatch.setattr(repository, "render_mute_preview", render)
    cue = review["filters"][0]
    response = app.send("GET", f"{route}/filters/{cue['id']}/mute-preview", headers)
    assert response.status_code == 200, response.text
    assert response.content == b"audio"
    assert rendered == [moved]


def test_a_different_file_at_the_renamed_path_is_rejected(app, moved_review):
    route, review, headers, _, moved = moved_review
    moved.write_bytes(b"replacement file with different sampled ranges")
    result = app.send(
        "POST", f"{route}/exports/edl/download", headers, {"revision": review["revision"]}
    )
    assert result.status_code == 409, result.text


@pytest.mark.parametrize("matches", [False, True])
@pytest.mark.parametrize("relocated", [False, True])
@pytest.mark.parametrize("kind", ["mute", "video"])
def test_saved_preview_keeps_the_analyzed_audio_track(
    app, moved_review, monkeypatch, matches, relocated, kind
):
    route, review, headers, initial, moved = moved_review
    if relocated:
        path = moved
    else:
        path = moved.rename(initial)
        item_id = review["target"]["item_id"]
        app.items[item_id] = app.items[item_id].model_copy(update={"path": str(path)})
    track = AudioTrack(4, "eng", None, "aac", 2, 0, 0.0, 60.0, "1/48000")
    original = AudioTrackSelection("a" * 64, track)
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review["id"])
        row.memberships[0].scan.analyzed_audio_selection_json = AudioSelection.from_selection(
            original
        ).model_dump_json()
        db.commit()
    rendered = []

    def select(current_path, stream_index):
        assert (current_path, stream_index) == (path, track.stream_index)
        return AudioTrackSelection("b" * 64, track if matches else replace(track, language="fra"))

    def render(path, *_args, audio_stream_index=None):
        rendered.append((Path(path), audio_stream_index))
        return b"audio"

    monkeypatch.setattr(repository_review_operations, "select_target_audio", select)
    monkeypatch.setattr(repository, f"render_{kind}_preview", render)
    wanted_source = "audio" if kind == "mute" else "visual"
    cue = next(value for value in review["filters"] if value["source"] == wanted_source)
    response = app.send("GET", f"{route}/filters/{cue['id']}/{kind}-preview", headers)
    assert response.status_code == (200 if matches else 409), response.text
    assert rendered == ([(path, track.stream_index)] if matches else [])


def test_renamed_preview_discards_output_if_file_changes_during_decode(
    app, moved_review, monkeypatch
):
    route, review, headers, _, moved = moved_review

    def replace_while_decoding(*_args, **_kwargs):
        moved.write_bytes(b"new generation during preview decode")
        return b"audio"

    monkeypatch.setattr(repository, "render_mute_preview", replace_while_decoding)
    response = app.send(
        "GET", f"{route}/filters/{review['filters'][0]['id']}/mute-preview", headers
    )
    assert response.status_code == 409, response.text


def test_renamed_draft_preview_rechecks_generation_without_explicit_version(
    app, moved_review, monkeypatch
):
    _, review, headers, _, moved = moved_review
    cue = review["filters"][0]
    item_id = review["target"]["item_id"]

    def replace_while_decoding(path, *_args, **_kwargs):
        assert Path(path) == moved
        moved.write_bytes(b"new generation during draft preview")
        return b"audio"

    monkeypatch.setattr(repository, "render_mute_preview", replace_while_decoding)
    response = app.send(
        "POST",
        f"/api/library-items/{item_id}/reviews/preview",
        headers,
        {
            "review_id": review["id"],
            "source_scan_id": cue["source_scan_id"],
            "source_finding_id": cue["source_finding_id"],
            "source": cue["source"],
            "edit_action": cue["edit_action"],
            "start_seconds": cue["start_seconds"],
            "end_seconds": cue["end_seconds"],
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "target_changed"
