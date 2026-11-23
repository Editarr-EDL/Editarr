"""Real review admission and filesystem sidecar publication for subtitles."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import scanner.api as root
from scanner.db import SavedReviewRow, ScanFindingRow, ScanRow
from scanner.http import review_subtitle_api
from scanner.models import (
    JellyfinMediaItem,
    JellyfinMediaVersion,
    McfCategoryFamily,
    ReviewCreate,
    ReviewScanMembership,
)
from scanner.persistence.repository import review_version_evidence
from scanner.persistence.repository.media_identity_store import (
    bind_review_version,
    observe_sampled_version,
)
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import PEER_USER, USER
from tests.test_review_access_http import app as app

_TEXT = (
    b"1\n00:00:01,000 --> 00:00:03,000\nDamn, close the gate.\n\n"
    b"2\n00:00:03,000 --> 00:00:06,000\nThis stays.\n"
)


def _seed(app):
    scan_id, _queue = app.seed_scan(USER, "Subtitle movie - alternate", status="complete")
    with app.accounts.factory() as db:
        scan = db.get(ScanRow, scan_id)
        selected = Path(scan.source_path)
        primary = app.file("Subtitle movie - primary.mkv")
        source = selected.with_suffix(".en.forced.srt")
        source.write_bytes(_TEXT)
        primary_subtitle = primary.with_suffix(".en.srt")
        primary_subtitle.write_bytes(_TEXT)

        def stream(path, title, forced=False):
            return {
                "Type": "Subtitle",
                "Index": 5,
                "Codec": "subrip",
                "IsExternal": True,
                "IsForced": forced,
                "DisplayTitle": title,
                "Language": "eng",
                "Path": str(path),
            }

        item_id = scan.media_item.jellyfin_id
        app.items[item_id] = JellyfinMediaItem(
            id=item_id,
            library_id=scan.media_item.library_id,
            name="Subtitle movie",
            item_type="Movie",
            path=str(primary),
            duration_seconds=60,
            media_streams=[stream(primary_subtitle, "Wrong version")],
            versions=[
                JellyfinMediaVersion(id="primary", path=str(primary)),
                JellyfinMediaVersion(
                    id="alternate",
                    path=str(selected),
                    subtitle_streams=[stream(source, "English forced", True)],
                ),
            ],
        )
        scan.duration_seconds = 60
        scan.media_source_id = "alternate"
        scan.scan_audio = True
        db.add(
            ScanFindingRow(
                scan_id=scan_id,
                source="audio",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                label="damn",
                start_seconds=1.25,
                end_seconds=1.75,
                selected=True,
                severity="medium",
                edit_action="mute",
            )
        )
        db.commit()
        review = root.create_saved_review(
            db,
            media_item_id=scan.media_item_id,
            media_source_id="alternate",
            data=ReviewCreate(
                name="Subtitle review",
                memberships=[ReviewScanMembership(scan_id=scan_id, role="audio")],
            ),
            duration_seconds=60,
        )
        row = db.get(SavedReviewRow, review.id)
        row.owner_id = row.original_author_id = USER
        db.commit()
    return review, source


def _request(app, review, endpoint, body, *, user=USER):
    return app.send(
        "POST",
        f"/api/reviews/{review.id}/subtitles/{endpoint}",
        app.accounts.unsafe_cookie(user),
        {"revision": review.revision} | body,
    )


def _pin_review_file(app, review):
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        scan = row.memberships[0].scan
        path = Path(scan.resolved_local_path)
        version_id = observe_sampled_version(
            db,
            media=row.media_item,
            media_source_id=scan.media_source_id,
            source_path=scan.source_path,
            fingerprint=sampled_media_fingerprint(path),
            scan_id=scan.id,
        )
        bind_review_version(db, row.id, version_id)
        db.commit()
    return path


def _selected_source(app, review):
    listed = _request(app, review, "sources", {})
    assert listed.status_code == 200, listed.text
    return {"source_id": listed.json()["sources"][0]["id"]}


def _preview_for_publication(app, review):
    selected = _selected_source(app, review)
    preview = _request(app, review, "preview", selected)
    assert preview.status_code == 200, preview.text
    return selected | {"preview_token": preview.json()["preview_token"]}


@pytest.mark.parametrize("endpoint", ["sources", "preview", "publish"])
def test_subtitle_fingerprint_leaves_other_database_writers_unblocked(app, monkeypatch, endpoint):
    app.client.app.include_router(review_subtitle_api.router)
    monkeypatch.setattr(review_subtitle_api, "_refresh", lambda *_: True)
    review, _source = _seed(app)
    media = _pin_review_file(app, review)
    if endpoint == "publish":
        payload = _preview_for_publication(app, review)
    elif endpoint == "preview":
        payload = _selected_source(app, review)
    else:
        payload = {}
    hashed = []

    def fingerprint(path):
        with closing(sqlite3.connect(app.accounts.path, timeout=0.1)) as other:
            other.execute("BEGIN IMMEDIATE")
            hashed.append(Path(path))
        return sampled_media_fingerprint(path)

    monkeypatch.setattr(review_version_evidence, "sampled_media_fingerprint", fingerprint)
    response = _request(app, review, endpoint, payload)
    assert response.status_code == 200, response.text
    assert hashed and set(hashed) == {media}


def test_subtitle_publish_refuses_replacement_after_fingerprint(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    review, source = _seed(app)
    media = _pin_review_file(app, review)
    payload = _preview_for_publication(app, review)
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
    response = _request(app, review, "publish", payload)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "subtitle_media_changed"
    assert replaced
    assert {path.name for path in media.parent.iterdir()} == names
    assert source.read_bytes() == _TEXT


def test_subtitle_export_denial_does_not_fingerprint_media(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    review, _source = _seed(app)
    _pin_review_file(app, review)
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
    denied = _request(app, review, "sources", {})
    assert denied.status_code in {403, 404}, denied.text


def test_exact_version_forced_preview_and_safe_repeat_publication(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    monkeypatch.setattr(review_subtitle_api, "_refresh", lambda *_: True)
    review, source = _seed(app)
    response = _request(app, review, "sources", {})
    assert response.status_code == 200, response.text
    sources = response.json()["sources"]
    assert len(sources) == 1
    assert sources[0]["label"] == "English forced"
    assert sources[0]["forced"] is True
    assert str(source.parent) not in response.text

    selected = {"source_id": sources[0]["id"]}
    preview = _request(app, review, "preview", selected).json()
    assert preview["changed_cues"] == 1
    assert preview["cues"][0]["filtered"] == "close the gate."
    payload = selected | {"preview_token": preview["preview_token"]}

    source.write_bytes(_TEXT + b"\n")
    changed = _request(app, review, "publish", payload)
    assert changed.status_code == 409, changed.text
    source.write_bytes(_TEXT)

    first = _request(app, review, "publish", payload)
    assert first.status_code == 200, first.text
    published = source.parent / first.json()["filename"]
    assert published.name.endswith(".en.forced.srt")
    assert not first.json()["replaced"]
    assert published.read_bytes() == _TEXT.replace(b"Damn, ", b"")
    assert source.read_bytes() == _TEXT
    repeated = _request(app, review, "publish", payload)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["filename"] == first.json()["filename"]
    assert repeated.json()["replaced"]
    published.write_bytes(b"replacement from another tool")
    failed = _request(app, review, "publish", payload)
    assert failed.status_code == 409, failed.text
    assert published.read_bytes() == b"replacement from another tool"


def test_hidden_review_and_publish_only_role(app):
    app.client.app.include_router(review_subtitle_api.router)
    review, source = _seed(app)
    hidden = _request(app, review, "sources", {}, user=PEER_USER)
    assert hidden.status_code == 404, hidden.text

    app.accounts.set_override(USER, C.EXPORTS_PERSONAL_DOWNLOAD_OWN, OverrideEffect.DENY)
    response = _request(app, review, "sources", {})
    assert response.status_code == 200, response.text
    source.unlink()
    unavailable = _request(app, review, "sources", {})
    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json()["fallback"] == "scan_required"


def test_download_only_review_export_access_can_publish_shared_subtitles(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    monkeypatch.setattr(review_subtitle_api, "_refresh", lambda *_: True)
    review, source = _seed(app)
    for capability in (C.EXPORTS_PERSONAL_PUBLISH_OWN, C.EXPORTS_DEFAULT_PUBLISH):
        app.accounts.set_override(USER, capability, OverrideEffect.DENY)

    listed = _request(app, review, "sources", {})
    assert listed.status_code == 200, listed.text
    selected = {"source_id": listed.json()["sources"][0]["id"]}
    preview = _request(app, review, "preview", selected)
    assert preview.status_code == 200, preview.text
    published = _request(
        app, review, "publish", selected | {"preview_token": preview.json()["preview_token"]}
    )
    assert published.status_code == 200, published.text
    assert (source.parent / published.json()["filename"]).read_bytes() == _TEXT.replace(
        b"Damn, ", b""
    )


def test_existing_subtitle_accepts_a_saved_review_without_scan_membership(app):
    app.client.app.include_router(review_subtitle_api.router)
    review, _source = _seed(app)
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        row.memberships.clear()
        db.commit()
    response = _request(app, review, "sources", {})
    assert response.status_code == 200, response.text
    assert response.json()["sources"][0]["supported"]


def test_malformed_external_subtitle_admits_track_bound_transcript_fallback(app, monkeypatch):
    from scanner.modeling.model_audio_tracks import AudioSelection, AudioTrackMetadata
    from scanner.persistence.repository import review_subtitles

    app.client.app.include_router(review_subtitle_api.router)
    review, source = _seed(app)
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
    response = _request(app, review, "sources", {})
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
