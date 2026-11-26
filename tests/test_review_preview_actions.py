"""A filter's preview follows its action, whatever its source or origin."""

import json

import pytest

import scanner.api as root
from scanner import repository
from scanner.db import ReviewFilterRow, ScanFindingRow, ScanRow
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import IMPORTS, app, seeded_review  # noqa: F401
from tests.test_review_community_preview_access import add_community_filter

COMBINATIONS = [("audio", "mute"), ("audio", "skip"), ("visual", "mute"), ("visual", "skip")]
MEDIA = {"mute": "audio/mpeg", "skip": "video/mp4"}


def record_renders(monkeypatch):
    rendered = []
    for name, action in [("render_mute_preview", "mute"), ("render_video_preview", "skip")]:
        monkeypatch.setattr(
            repository,
            name,
            lambda _media, start, end, action=action, **_options: (
                rendered.append((action, start, end)) or action.encode()
            ),
        )
    return rendered


def assert_rendered(response, rendered, action, start, end):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == MEDIA[action]
    assert response.content == action.encode()
    assert rendered[-1] == (action, start, end)


@pytest.mark.parametrize(("source", "action"), COMBINATIONS)
def test_unsaved_draft_preview_renders_its_action(app, monkeypatch, source, action):  # noqa: F811
    review = seeded_review(app, USER, f"draft-{source}-{action}")
    rendered = record_renders(monkeypatch)
    route = f"/api/library-items/{review.target.item_id}/reviews/preview"
    body = {"source": source, "edit_action": action, "start_seconds": 3, "end_seconds": 5}
    response = app.send("POST", route, app.accounts.unsafe_cookie(USER), body)
    assert_rendered(response, rendered, action, 3, 5)


@pytest.mark.parametrize(("source", "action"), COMBINATIONS)
def test_edited_scan_finding_preview_renders_its_action(app, monkeypatch, source, action):  # noqa: F811
    review = seeded_review(app, USER, f"finding-{source}-{action}")
    scan_id = review.memberships[0].scan_id
    with app.accounts.factory() as db:
        db.get(ScanRow, scan_id).scan_audio = True
        finding = ScanFindingRow(scan_id=scan_id, source=source, start_seconds=10, end_seconds=12)
        db.add(finding)
        db.commit()
        finding_id = finding.id
    rendered = record_renders(monkeypatch)
    route = f"/api/library-items/{review.target.item_id}/reviews/preview"
    body = {
        "review_id": review.id,
        "source_scan_id": scan_id,
        "source_finding_id": finding_id,
        "source": source,
        "edit_action": action,
        "start_seconds": 10,
        "end_seconds": 12,
    }
    response = app.send("POST", route, app.accounts.unsafe_cookie(USER), body)
    assert_rendered(response, rendered, action, 10, 12)


@pytest.mark.parametrize("action", ["mute", "skip"])
def test_community_filter_preview_renders_its_edited_action(app, monkeypatch, action):  # noqa: F811
    review = seeded_review(app, USER, f"community-{action}")
    add_community_filter(app, review.id)
    rendered = record_renders(monkeypatch)
    route = f"/api/library-items/{review.target.item_id}/reviews/preview"
    body = {
        "review_id": review.id,
        "source": "audio",
        "edit_action": action,
        "start_seconds": 20,
        "end_seconds": 25,
    }
    response = app.send("POST", route, app.accounts.unsafe_cookie(USER), body)
    assert_rendered(response, rendered, action, 20, 25)


def test_saved_filter_preview_follows_its_saved_action(app, monkeypatch):  # noqa: F811
    item_id = app.jellyfin_item(guid("saved-skip-preview"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60)
    imported = app.client.post(
        "/api/edl-imports/upload",
        headers=app.accounts.unsafe_cookie(USER),
        data={
            "options": json.dumps(
                {"target": {"source": "jellyfin", "item_id": item_id}, "name": "preview"}
            )
        },
        files={"file": ("preview.edl", IMPORTS["edl"])},
    )
    assert imported.status_code == 201, imported.text
    review = imported.json()["review"]
    saved = review["filters"][0]
    assert (saved["source"], saved["edit_action"]) == ("audio", "mute")
    with app.accounts.factory() as db:
        row = db.get(ReviewFilterRow, (review["id"], saved["id"]))
        row.edit_action = "skip"
        db.commit()
    rendered = record_renders(monkeypatch)
    route = f"/api/reviews/{review['id']}/filters/{saved['id']}"
    headers = app.accounts.cookie(USER)
    video = app.send("GET", f"{route}/video-preview", headers)
    assert_rendered(video, rendered, "skip", saved["start_seconds"], saved["end_seconds"])
    mute = app.send("GET", f"{route}/mute-preview", headers)
    assert mute.status_code == 409, mute.text
    assert len(rendered) == 1

