"""Retain and transfer consent names each visible publication by identity and revision.

A re-publication, an equal-count replacement, or a publish racing a preview that
skipped the publication lock changes the confirmation, so the confirmed attempt,
recomputed under that lock, refuses it as stale. Publications of reviews the actor
may not see, or whose files the export rules hide (MU-36, MU-37), stay a count.
Review rounds 3 (R3-F2) and 4 (R4-E), through the real routes.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest

from scanner.http import review_export_api
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, USER
from tests.test_account_deletion_http import app as app
from tests.test_account_deletion_http import code, delete, exports_app, preview
from tests.test_personal_exports_http import publish, review_app, seeded_review

__all__ = ["exports_app", "review_app"]
STALE = (409, "stale_account_deletion")


def _destination(mode):
    return ADMIN if mode == "transfer" else None


def _withdraw(app, publication):
    path = "/api/exports/{id}?revision={publication_revision}&publication_id={publication_id}"
    response = app.send("DELETE", path.format(**publication), app.accounts.unsafe_cookie(USER))
    assert response.status_code == 204, response.text


@pytest.mark.parametrize("mode", ["retain", "transfer"])
@pytest.mark.parametrize("change", ["republish", "replace"])
def test_a_changed_publication_invalidates_consent_at_an_equal_count(app, mode, change):
    review = seeded_review(app, USER, "changing")
    first = publish(app, review, "edl").json()
    body = preview(app, ADMIN, USER, mode, _destination(mode)).json()
    assert [item["id"] for item in body["publications"]["items"]] == [first["id"]]
    if change == "republish":
        assert publish(app, review, "edl").status_code == 200
    else:
        _withdraw(app, first)
        assert publish(app, review, "mcf").status_code == 200
    current = preview(app, ADMIN, USER, mode, _destination(mode)).json()
    assert current["personal_files"] == body["personal_files"]
    assert current["confirmation"] != body["confirmation"]
    refused = delete(app, ADMIN, USER, mode, body["confirmation"], destination=_destination(mode))
    assert code(refused) == STALE, refused.text
    applied = delete(
        app, ADMIN, USER, mode, current["confirmation"], destination=_destination(mode)
    )
    assert applied.status_code == 200, applied.text


@pytest.mark.parametrize("mode", ["retain", "transfer", "delete"])
def test_a_preview_beside_a_held_publish_cannot_confirm_its_result(app, monkeypatch, mode):
    review = seeded_review(app, USER, "racing")
    assert publish(app, review, "edl").status_code == 200
    entered, release = Event(), Event()
    original = review_export_api.publication_lock

    @contextmanager
    def held(db):
        with original(db):
            entered.set()
            assert release.wait(20)
            yield

    with monkeypatch.context() as patch:
        patch.setattr(review_export_api, "publication_lock", held)
        with ThreadPoolExecutor(max_workers=1) as pool:
            publishing = pool.submit(publish, app, review, "edl")
            assert entered.wait(20)
            try:
                response = preview(app, ADMIN, USER, mode, _destination(mode))
            finally:
                release.set()
            assert publishing.result(30).status_code == 200
    assert response.status_code == 200, response.text
    choices = [(file["publication"]["id"], "retain") for file in response.json()["files"]]
    refused = delete(
        app,
        ADMIN,
        USER,
        mode,
        response.json()["confirmation"],
        choices,
        destination=_destination(mode),
    )
    assert code(refused) == STALE, refused.text


def test_publications_of_reviews_the_actor_cannot_see_are_only_a_count(app):
    review = seeded_review(app, USER, "private")
    publication = publish(app, review, "edl").json()
    app.accounts.set_override(ADMIN, C.REVIEWS_VIEW_OTHER, OverrideEffect.DENY)
    response = preview(app, ADMIN, USER, "retain")
    assert response.json()["publications"] == {"hidden": 1, "items": []}
    assert publication["publication_id"] not in response.text
    assert publish(app, review, "edl").status_code == 200
    again = preview(app, ADMIN, USER, "retain").json()
    assert again["confirmation"] == response.json()["confirmation"]
    applied = delete(app, ADMIN, USER, "retain", again["confirmation"])
    assert applied.status_code == 200, applied.text
    assert publication["publication_id"] not in applied.text


@pytest.mark.parametrize("mode", ["retain", "delete"])
def test_publications_the_export_rules_hide_are_only_counted(app, mode):
    review = seeded_review(app, USER, "export-hidden")
    ids = [publish(app, review, fmt).json()["id"] for fmt in ("edl", "mcf")]
    shown = preview(app, ADMIN, USER, mode).json()["publications"]
    assert [item["id"] for item in shown["items"]] == ids and shown["hidden"] == 0
    app.accounts.set_override(ADMIN, C.EXPORTS_PERSONAL_DELETE_OTHER, OverrideEffect.DENY)
    assert app.send("GET", f"/api/exports/{ids[0]}", app.accounts.cookie(ADMIN)).status_code == 404
    body = preview(app, ADMIN, USER, mode).json()
    assert body["publications"] == {"hidden": 2, "items": []}
    assert body["files"] == []
