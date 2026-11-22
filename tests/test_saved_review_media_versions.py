"""Saved reviews stay bound to the exact file generation they were created for."""

from pathlib import Path

from scanner.persistence.repository.media_identity_store import review_version_id
from tests.multi_user_access_support import USER
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
