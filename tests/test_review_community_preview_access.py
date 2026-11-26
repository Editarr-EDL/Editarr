"""Previewing a saved community filter needs the preview permission, not cue editing."""

from datetime import UTC, datetime

from scanner import repository
from scanner.db import ReviewFilterRow
from scanner.persistence.rows.db_review_community import ReviewCommunitySourceRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import USER
from tests.test_review_access_http import app, seeded_review  # noqa: F401


def add_community_filter(served, review_id):
    with served.accounts.factory() as db:
        db.add(
            ReviewCommunitySourceRow(
                review_id=review_id,
                id="community-source",
                position=0,
                community_version_id="version-1",
                label="Theatrical",
                master_revision=1,
                filters_json="[]",
                filters_digest="0" * 64,
                captured_at=datetime.now(UTC),
            )
        )
        db.flush()
        db.add(
            ReviewFilterRow(
                review_id=review_id,
                id="community-filter",
                community_source_id="community-source",
                community_filter_id="master-filter",
                position=99,
                source="audio",
                label="word",
                details=None,
                group_key=None,
                start_seconds=20,
                end_seconds=25,
                confidence=None,
                edit_action="mute",
                selected=True,
            )
        )
        db.commit()


def test_preview_only_account_previews_unchanged_saved_community_filter(app, monkeypatch):  # noqa: F811
    review = seeded_review(app, USER, "community-preview")
    add_community_filter(app, review.id)
    rendered = []

    def render(media, start, end):
        rendered.append((start, end))
        return b"preview audio"

    monkeypatch.setattr(repository, "render_mute_preview", render)
    app.accounts.set_override(USER, C.REVIEWS_CUES_EDIT_OWN, OverrideEffect.DENY)
    headers = app.accounts.unsafe_cookie(USER)
    route = f"/api/library-items/{review.target.item_id}/reviews/preview"
    body = {
        "review_id": review.id,
        "source": "audio",
        "edit_action": "mute",
        "start_seconds": 20,
        "end_seconds": 25,
    }

    saved = app.send("POST", route, headers, body)
    assert saved.status_code == 200, saved.text
    assert saved.content == b"preview audio"

    # An edited (unsaved) clip is arbitrary media access and still needs cue editing.
    edited = app.send("POST", route, headers, {**body, "start_seconds": 21})
    assert edited.status_code == 403, edited.text
    assert rendered == [(20, 25)]

    app.accounts.set_override(USER, C.REVIEWS_CUES_EDIT_OWN, OverrideEffect.ALLOW)
    allowed = app.send("POST", route, headers, {**body, "start_seconds": 21})
    assert allowed.status_code == 200, allowed.text

    app.accounts.set_override(USER, C.REVIEWS_PREVIEW_OWN, OverrideEffect.DENY)
    denied = app.send("POST", route, headers, body)
    assert denied.status_code == 403, denied.text
    assert rendered == [(20, 25), (21, 25)]
