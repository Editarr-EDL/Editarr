"""Saved reviews stay bound to the exact file generation they were created for."""

from hashlib import sha256
from pathlib import Path

import pytest

from scanner.db import SavedReviewRow
from scanner.persistence.repository.media_identity_store import (
    bind_review_version,
    observe_version,
    review_version_id,
)
from scanner.runtime import media_fingerprints
from tests.multi_user_access_support import PEER_USER, USER
from tests.test_review_access_http import app as app
from tests.test_review_subtitle_http import _seed


def test_saved_empty_reviews_and_duplicates_keep_original_file_generation(app):
    from scanner.http import review_subtitle_api

    app.client.app.include_router(review_subtitle_api.router)
    old_unpinned, _subtitle = _seed(app)
    item_id = old_unpinned.target.item_id
    media = Path(app.items[item_id].versions[1].path)
    route = f"/api/library-items/{item_id}/reviews"
    headers = app.accounts.unsafe_cookie(USER)
    slot = {"media_source_id": "alternate"}

    def create(name, **extra):
        return app.send("POST", route, headers, slot | {"name": name} | extra)

    def listed(generation=None):
        query = "?media_source_id=alternate"
        if generation is not None:
            query += f"&stable_version_id={generation}"
        response = app.send("GET", route + query, app.accounts.cookie(USER))
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()}

    original = create("Original empty review")
    assert original.status_code == 201, original.text
    original_id = original.json()["id"]
    assert original.json()["memberships"] == []
    with app.accounts.factory() as db:
        first = review_version_id(db, original_id)
    assert first is not None
    assert listed(first) == {original_id}
    assert listed() == {original_id, old_unpinned.id}
    assert (
        app.send(
            "POST", f"/api/reviews/{original_id}/subtitles/sources", headers, {"revision": 0}
        ).status_code
        == 200
    )

    media.write_bytes(b"replacement physical generation")
    stale = create("Must not create", stable_version_id=first)
    assert stale.status_code == 409, stale.text
    assert listed(first) == set()
    assert listed() == {original_id, old_unpinned.id}
    replacement = create("Current generation")
    assert replacement.status_code == 201, replacement.text
    replacement_id = replacement.json()["id"]
    with app.accounts.factory() as db:
        second = review_version_id(db, replacement_id)
    assert second is not None and second != first
    assert listed(second) == {replacement_id}

    duplicate = app.send(
        "POST",
        f"/api/reviews/{original_id}/duplicate",
        headers,
        {"revision": 0, "name": "Original historical copy"},
    )
    assert duplicate.status_code == 201, duplicate.text
    copy_id = duplicate.json()["id"]
    with app.accounts.factory() as db:
        assert review_version_id(db, copy_id) == first
    assert listed(second) == {replacement_id}

    for review_id in (original_id, copy_id):
        response = app.send(
            "POST", f"/api/reviews/{review_id}/subtitles/sources", headers, {"revision": 0}
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "subtitle_media_changed"


def _pinned_list_target(app, *, bind):
    review, _ = _seed(app)
    item_id = review.target.item_id
    media = Path(app.items[item_id].versions[1].path)
    with app.accounts.factory() as db:
        saved = db.get(SavedReviewRow, review.id)
        version_id = observe_version(
            db,
            media=saved.media_item,
            media_source_id="alternate",
            source_path=str(media),
            resolved_path=str(media),
            content_sha256=sha256(media.read_bytes()).hexdigest(),
        )
        if bind:
            bind_review_version(db, review.id, version_id)
        db.commit()
    route = (
        f"/api/library-items/{item_id}/reviews"
        f"?media_source_id=alternate&stable_version_id={version_id}"
    )
    return route, media, review.id


@pytest.mark.parametrize("has_hidden_review", [False, True])
def test_pinned_list_skips_all_fingerprint_work_without_visible_reviews(
    app, monkeypatch, has_hidden_review
):
    from scanner.http import review_target_routes

    route, _, _ = _pinned_list_target(app, bind=has_hidden_review)

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("An empty review list inspected media fingerprint evidence")

    monkeypatch.setattr(review_target_routes, "version_file_matches", unexpected_read)
    monkeypatch.setattr(review_target_routes, "cached_target_digest", unexpected_read)
    user = PEER_USER if has_hidden_review else USER
    response = app.send("GET", route, app.accounts.cookie(user))
    assert response.status_code == 200, response.text
    assert response.json() == []


def test_pinned_list_uses_proven_digest_and_rejects_a_replaced_media_file(
    app, monkeypatch, tmp_path
):
    from scanner.http import review_target_routes

    route, media, review_id = _pinned_list_target(app, bind=True)
    monkeypatch.setattr(media_fingerprints.settings, "data_dir", tmp_path / "editarr-data")
    media_fingerprints.content_digest(media)

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("The review list already had an exact fingerprint for this generation")

    with monkeypatch.context() as patched:
        patched.setattr(review_target_routes, "version_file_matches", unexpected_read)
        response = app.send("GET", route, app.accounts.cookie(USER))
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()] == [review_id]

    media.write_bytes(b"replacement bytes in the same Jellyfin slot")
    response = app.send("GET", route, app.accounts.cookie(USER))
    assert response.status_code == 200, response.text
    assert response.json() == []
