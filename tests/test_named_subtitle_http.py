"""Published Jellyfin names and their authorization across accounts and review changes."""

from pathlib import Path

import pytest

import scanner.api  # noqa: F401 - initialize the API before importing its routes
from scanner.db import SavedReviewRow, ScanRow
from scanner.http import review_subtitle_api, subtitle_publications_api
from scanner.modeling.model_subtitles import SubtitleSource
from scanner.runtime.subtitle_sidecars import media_slot, publish_sidecar
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import PEER_USER, USER
from tests.subtitle_review_http_support import (
    SUBTITLE_TEXT,
    link_publisher,
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

_FILTERED = SUBTITLE_TEXT.replace(b"Damn, ", b"")


@pytest.fixture(autouse=True)
def routes(app, monkeypatch):
    app.client.app.include_router(review_subtitle_api.router)
    app.client.app.include_router(subtitle_publications_api.router)
    monkeypatch.setattr(review_subtitle_api, "_refresh", lambda *_: True)


def test_same_review_gets_separate_named_tracks_and_shared_visibility(app):
    review, source = seed_subtitle_review(app)
    media = pin_review_file(app, review)
    link_publisher(app, PEER_USER, "Café")
    for capability in (C.REVIEWS_VIEW_OTHER, C.REVIEWS_EXPORT_OTHER):
        app.accounts.set_override(PEER_USER, capability, OverrideEffect.ALLOW)

    legacy = SubtitleSource(
        id="c" * 64,
        label="Original",
        language="eng",
        format="srt",
        kind="external",
        forced=True,
    )
    publish_sidecar(media, media_slot(media, "alternate"), legacy, b"older shared copy")
    path = media.with_name(f"{media.stem}.Editarr Filtered {'c' * 16}.eng.forced.srt")
    sam = preview_for_publication(app, review)
    copied = subtitle_request(app, review, "publish", sam, user=PEER_USER)
    assert copied.status_code == 409, copied.text
    assert copied.json()["detail"]["code"] == "subtitle_source_changed"
    sam_result = subtitle_request(app, review, "publish", sam)
    assert sam_result.status_code == 200, sam_result.text
    cafe = preview_for_publication(app, review, user=PEER_USER)
    cafe_result = subtitle_request(app, review, "publish", cafe, user=PEER_USER)
    assert cafe_result.status_code == 200, cafe_result.text

    sam_file, cafe_file = [
        source.parent / response.json()["filename"] for response in (sam_result, cafe_result)
    ]
    assert sam_file.name == f"{media.stem}.Sam - Subtitle review.en.forced.srt"
    assert cafe_file.name == f"{media.stem}.Café - Subtitle review.en.forced.srt"
    assert sam_file.read_bytes() == cafe_file.read_bytes() == _FILTERED
    assert path.read_bytes() == b"older shared copy"

    with app.accounts.factory() as db:
        item_id = db.get(SavedReviewRow, review.id).media_item.jellyfin_id
    for viewer, own, other in ((USER, sam_file, cafe_file), (PEER_USER, cafe_file, sam_file)):
        listed = app.send(
            "GET",
            f"/api/media/subtitles/publications?item_id={item_id}&media_source_id=alternate",
            app.accounts.cookie(viewer),
        )
        assert listed.status_code == 200, listed.text
        by_file = {item["filename"]: item for item in listed.json()["published_subtitles"]}
        assert by_file[own.name]["owned_by_viewer"] is True
        assert by_file[other.name]["owned_by_viewer"] is False
        assert by_file[path.name]["owned_by_viewer"] is False
        assert {by_file[filename]["label"] for filename in (own.name, other.name)} == {
            "Sam - Subtitle review",
            "Café - Subtitle review",
        }


def test_account_rename_requires_new_preview_and_retires_only_prior_name(app):
    review, source = seed_subtitle_review(app)
    old = preview_for_publication(app, review)
    original = subtitle_request(app, review, "publish", old)
    assert original.status_code == 200, original.text
    old_file = source.parent / original.json()["filename"]
    link_publisher(app, USER, "Samantha")
    stale = subtitle_request(app, review, "publish", old)
    assert stale.status_code == 409, stale.text
    assert old_file.read_bytes() == _FILTERED
    new = preview_for_publication(app, review)
    published = subtitle_request(app, review, "publish", new)
    assert published.status_code == 200, published.text
    assert published.json()["replaced"] is True
    new_file = source.parent / published.json()["filename"]
    assert ".Samantha - Subtitle review." in new_file.name
    assert new_file.read_bytes() == _FILTERED
    assert not old_file.exists()
    assert source.read_bytes() == SUBTITLE_TEXT


@pytest.mark.parametrize("case", ["unlinked", "reserved", "collision"])
def test_preview_remains_available_without_an_exact_writable_name(app, case):
    review, source = seed_subtitle_review(app, linked=case != "unlinked")
    conflict = None
    if case == "reserved":
        link_publisher(app, USER, "Forced Name")
    elif case == "collision":
        stem = source.name.removesuffix(".en.forced.srt")
        conflict = source.with_name(f"{stem}.Sam - Subtitle review.en.forced.srt")
        conflict.write_bytes(b"unmanaged")
    selected = selected_source(app, review)
    preview = subtitle_request(app, review, "preview", selected)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["changed_cues"] == 1
    assert result["cues"][0]["filtered"] == "close the gate."
    assert result["publication_block_reason"]
    assert result["preview_token"] is result["filename"] is None
    denied = subtitle_request(app, review, "publish", selected | {"preview_token": "a" * 64})
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"]["message"] == result["publication_block_reason"]
    if conflict is not None:
        assert conflict.read_bytes() == b"unmanaged"


@pytest.mark.parametrize(
    ("language", "expected_suffix"),
    [("wel", "wel"), ("zz", None), ("pob", "pt-br"), ("pb", "pt-br"), ("pop", "pt-pt")],
)
def test_publication_uses_language_aliases_from_jellyfin_cultures(app, language, expected_suffix):
    review, source = seed_subtitle_review(app)
    with app.accounts.factory() as db:
        item = db.get(SavedReviewRow, review.id).media_item
        stream = app.items[item.jellyfin_id].versions[1].subtitle_streams[0]
        stream.update(Language=language, IsHearingImpaired=True)

    selected = selected_source(app, review)
    preview = subtitle_request(app, review, "preview", selected)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["source"]["language"] == language
    assert result["source"]["forced"] and result["source"]["sdh"]
    assert result["changed_cues"] == 1
    assert result["cues"][0]["filtered"] == "close the gate."
    if expected_suffix is None:
        assert result["preview_token"] is result["filename"] is None
        files_before = set(source.parent.iterdir())
        denied = subtitle_request(app, review, "publish", selected | {"preview_token": "a" * 64})
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"]["code"] == "subtitle_language_unavailable"
        assert denied.json()["detail"]["message"] == result["publication_block_reason"]
        assert set(source.parent.iterdir()) == files_before
    else:
        stem = source.name.removesuffix(".en.forced.srt")
        filename = f"{stem}.Sam - Subtitle review.{expected_suffix}.forced.sdh.srt"
        assert result["filename"] == filename
        assert result["track_title"] == "Sam - Subtitle review"
        assert result["publication_block_reason"] is None
        published = subtitle_request(
            app, review, "publish", selected | {"preview_token": result["preview_token"]}
        )
        assert published.status_code == 200, published.text
        assert published.json()["filename"] == filename
        assert published.json()["source"]["forced"] and published.json()["source"]["sdh"]
        assert source.with_name(filename).read_bytes() == _FILTERED
    assert source.read_bytes() == SUBTITLE_TEXT


def test_jellyfin_catalog_keeps_published_file_out_of_source_picker(app):
    review, _ = seed_subtitle_review(app)
    prepared = preview_for_publication(app, review)
    result = subtitle_request(app, review, "publish", prepared)
    assert result.status_code == 200, result.text
    filename = result.json()["filename"]
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        path = Path(db.get(ScanRow, row.memberships[0].scan_id).resolved_local_path)
        streams = app.items[row.media_item.jellyfin_id].versions[1].subtitle_streams
        streams.append(streams[0] | {"Index": 6, "Path": str(path.with_name(filename))})
    listed = subtitle_request(app, review, "sources", {})
    assert listed.status_code == 200, listed.text
    assert [track["id"] for track in listed.json()["sources"]] == [prepared["source_id"]]
    assert listed.json()["sources"][0]["published_filename"] == filename
    assert listed.json()["published_subtitles"][0]["owned_by_viewer"] is True


@pytest.mark.parametrize(
    ("sibling", "blocked"),
    [
        (".Sam - Subtitle review.mkv", True),
        (".SAM - SUBTITLE REVIEW.EN.FORCED.MP4", True),
        (".Sam - Subtitle reviewX.mkv", False),
        (".Sam - Subtitle review.nfo", False),
    ],
)
def test_named_sidecar_is_only_for_its_selected_video(app, sibling, blocked):
    review, source = seed_subtitle_review(app)
    media_stem = source.name.removesuffix(".en.forced.srt")
    source.with_name(media_stem + sibling).write_bytes(b"sibling")
    selected = selected_source(app, review)
    preview = subtitle_request(app, review, "preview", selected)
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["changed_cues"] == 1
    assert result["cues"][0]["filtered"] == "close the gate."
    if blocked:
        assert result["preview_token"] is result["filename"] is None
        assert "more than one video" in result["publication_block_reason"]
        denied = subtitle_request(app, review, "publish", selected | {"preview_token": "a" * 64})
        assert denied.status_code == 409, denied.text
        assert denied.json()["detail"]["code"] == "subtitle_version_ambiguous"
    else:
        assert result["preview_token"] and result["filename"]
        published = subtitle_request(
            app, review, "publish", selected | {"preview_token": result["preview_token"]}
        )
        assert published.status_code == 200, published.text


def test_catalog_sibling_blocks_preview_before_local_file_appears(app):
    review, source = seed_subtitle_review(app)
    media_stem = source.name.removesuffix(".en.forced.srt")
    sibling = source.with_name(f"{media_stem}.Sam - Subtitle review.mkv")
    with app.accounts.factory() as db:
        item = app.items[db.get(SavedReviewRow, review.id).media_item.jellyfin_id]
        item.versions.append(
            item.versions[0].model_copy(
                update={"id": "third", "path": str(sibling)},
            )
        )
    selected = selected_source(app, review)
    preview = subtitle_request(app, review, "preview", selected)
    assert preview.status_code == 200, preview.text
    assert "more than one video" in preview.json()["publication_block_reason"]
    assert preview.json()["cues"][0]["filtered"] == "close the gate."


def test_publish_rechecks_sibling_after_preparing_to_write(app, monkeypatch):
    review, source = seed_subtitle_review(app)
    prepared = preview_for_publication(app, review)
    media_stem = source.name.removesuffix(".en.forced.srt")
    sibling = source.with_name(f"{media_stem}.Sam - Subtitle review.mkv")
    require_same_subtitle_input = review_subtitle_api.require_same_subtitle_input

    def create_sibling_after_check(*args):
        require_same_subtitle_input(*args)
        sibling.write_bytes(b"appeared after rendering")

    monkeypatch.setattr(
        review_subtitle_api, "require_same_subtitle_input", create_sibling_after_check
    )
    refused = subtitle_request(app, review, "publish", prepared)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "subtitle_version_ambiguous"
    assert not source.with_name(f"{media_stem}.Sam - Subtitle review.en.forced.srt").exists()
