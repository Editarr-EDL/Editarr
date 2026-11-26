"""Previewing a saved community filter needs the preview permission, not cue editing."""

from datetime import UTC, datetime
from pathlib import Path

from scanner import repository
from scanner.db import ReviewFilterRow, SavedReviewRow, ScanRow
from scanner.persistence.repository import media_identity_store as identity
from scanner.persistence.rows.db_review_community import ReviewCommunitySourceRow
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import USER
from tests.test_review_access_http import app, seeded_review  # noqa: F401


def observe_review_file(db, review_id: str) -> tuple[str, Path]:
    """Record the review's current file as a sampled local version."""
    row = db.get(SavedReviewRow, review_id)
    scan = db.get(ScanRow, row.memberships[0].scan_id)
    version = identity.observe_sampled_version(
        db,
        media=row.media_item,
        media_source_id=None,
        source_path=scan.source_path,
        fingerprint=sampled_media_fingerprint(scan.source_path),
    )
    return version, Path(scan.source_path)


def add_community_filter(served, review_id, *, pin_review=True):
    """Add a saved community filter; `pin_review` binds the review to its current file,
    which places the snapshot's times on that file's timeline."""
    with served.accounts.factory() as db:
        if pin_review:
            identity.bind_review_version(db, review_id, observe_review_file(db, review_id)[0])
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
