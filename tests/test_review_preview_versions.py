"""Review previews play only the media version whose timeline the filter belongs to."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from scanner import repository
from scanner.db import CommunityMasterLinkRow, ReviewCommunitySourceRow
from scanner.persistence.repository import media_identity_store as identity
from tests.multi_user_access_support import USER
from tests.test_review_access_http import app, seeded_review  # noqa: F401
from tests.test_review_community_preview_access import add_community_filter, observe_review_file

CLIP = {"source": "audio", "edit_action": "mute", "start_seconds": 20, "end_seconds": 25}


def _link(db, version: str) -> CommunityMasterLinkRow:
    now = datetime.now(UTC)
    link = CommunityMasterLinkRow(
        id="community-link",
        owner_id=USER,
        media_version_id=version,
        service_url="https://community.example",
        community_version_id="version-1",
        version_status="confirmed",
        applied_revision=1,
        latest_revision=1,
        alignment_state="not_required",
        link_state="active",
        created_at=now,
        updated_at=now,
    )
    db.add(link)
    db.flush()
    return link


@pytest.fixture
def renders(monkeypatch):
    rendered = []
    monkeypatch.setattr(
        repository,
        "render_mute_preview",
        lambda _media, start, end, **_options: rendered.append((start, end)) or b"audio",
    )
    return rendered


def community_review(app, name: str, binding: str) -> tuple[str, str, Path]:  # noqa: F811
    """A review with a saved community filter whose timeline is pinned by `binding`."""
    review = seeded_review(app, USER, name)
    add_community_filter(app, review.id, pin_review=False)
    with app.accounts.factory() as db:
        version, path = observe_review_file(db, review.id)
        if binding == "link":
            link = _link(db, version)
            db.get(ReviewCommunitySourceRow, (review.id, "community-source")).link_id = link.id
        elif binding == "review":
            identity.bind_review_version(db, review.id, version)
        db.commit()
    return review.id, review.target.item_id, path


def preview(app, item_id: str, body: dict):  # noqa: F811
    route = f"/api/library-items/{item_id}/reviews/preview"
    return app.send("POST", route, app.accounts.unsafe_cookie(USER), body)


def assert_changed(response):
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "target_changed"


@pytest.mark.parametrize("binding", ["link", "review"])
def test_community_filter_preview_refuses_replaced_media(app, renders, binding):  # noqa: F811
    review_id, item_id, path = community_review(app, f"community-{binding}", binding)
    body = {"review_id": review_id, **CLIP}
    assert preview(app, item_id, body).status_code == 200

    path.write_bytes(b"a replacement file with a different timeline")
    assert_changed(preview(app, item_id, body))
    assert renders == [(20, 25)]


def test_community_filter_preview_without_a_known_version_is_refused(app, renders):  # noqa: F811
    review_id, item_id, _path = community_review(app, "community-unknown", "none")
    assert_changed(preview(app, item_id, {"review_id": review_id, **CLIP}))
    assert renders == []


def test_saved_review_draft_preview_refuses_a_replaced_bound_version(app, renders):  # noqa: F811
    review = seeded_review(app, USER, "bound-draft")
    with app.accounts.factory() as db:
        version, path = observe_review_file(db, review.id)
        identity.bind_review_version(db, review.id, version)
        db.commit()
    body = {"review_id": review.id, **CLIP}
    assert preview(app, review.target.item_id, body).status_code == 200

    path.write_bytes(b"a replacement file with a different timeline")
    assert_changed(preview(app, review.target.item_id, body))
    assert renders == [(20, 25)]


def test_saved_community_filter_preview_refuses_replaced_media(app, renders):  # noqa: F811
    # The saved endpoint renders scan evidence only; community clips use the draft endpoint.
    review_id, _item_id, path = community_review(app, "community-saved", "link")
    path.write_bytes(b"a replacement file with a different timeline")
    route = f"/api/reviews/{review_id}/filters/community-filter/mute-preview"
    response = app.send("GET", route, app.accounts.cookie(USER))
    assert response.status_code == 409, response.text
    assert renders == []


def test_alignment_clip_refuses_a_file_that_is_no_longer_the_linked_version(app, renders):  # noqa: F811
    # Alignment clips are unsaved drafts at aligned times for the link's local version.
    review = seeded_review(app, USER, "alignment-clip")
    with app.accounts.factory() as db:
        version, path = observe_review_file(db, review.id)
        db.commit()
    body = {"stable_version_id": version, **CLIP}
    assert preview(app, review.target.item_id, body).status_code == 200

    path.write_bytes(b"a replacement file with a different timeline")
    assert_changed(preview(app, review.target.item_id, body))
    assert renders == [(20, 25)]
