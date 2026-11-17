"""Private review IDs must not reveal whether another account's review exists."""

import json

import pytest

import scanner.api as root
from scanner import repository
from scanner.db import SavedReviewRow, ScanFindingRow, ScanRow
from scanner.http import (
    catalog_targets,
    review_edit_api,
    review_import_routes_api,
    review_target_routes,
)
from scanner.models import (
    JellyfinLibrary,
    JellyfinMediaItem,
    JellyfinMediaVersion,
    ReviewCreate,
    ReviewScanMembership,
)
from scanner.persistence.repository.review_access import RENAME, ReviewAccess
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, PEER_USER, USER, live_accounts
from tests.scan_access_http import LIBRARY_A, guid, serve_scans


@pytest.fixture
def app(monkeypatch, tmp_path):
    catalog_targets.jellyfin_libraries.clear()
    accounts = live_accounts(tmp_path)
    served = serve_scans(monkeypatch, accounts, tmp_path)
    monkeypatch.setattr(
        root.JellyfinClient,
        "libraries",
        lambda: [JellyfinLibrary(id="a" * 32, name="Movies", locations=[str(served.media)])],
        raising=False,
    )
    served.client.app.include_router(review_edit_api.router)
    served.client.app.include_router(review_target_routes.router)
    served.client.app.include_router(review_import_routes_api.router)
    yield served
    catalog_targets.jellyfin_libraries.clear()
    served.client.close()
    accounts.close()


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_hidden_and_absent_reviews_are_identical(app, credential, method):
    scan_id, _queue = app.seed_scan(PEER_USER, "foreign", status="complete")
    review_id = app.seed_review(PEER_USER, "private", scan_id)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    body = {"name": "changed", "revision": 1} if method == "PATCH" else None
    hidden = app.send(method, f"/api/reviews/{review_id}", headers, body)
    absent = app.send(method, "/api/reviews/absent", headers, body)
    assert hidden.status_code == absent.status_code == 404
    assert hidden.content == absent.content
    assert hidden.headers == absent.headers


def seeded_review(app, owner, name):
    scan_id, _queue = app.seed_scan(owner, name, status="complete")
    with app.accounts.factory() as db:
        scan = db.get(ScanRow, scan_id)
        app.items[scan.media_item.jellyfin_id] = JellyfinMediaItem(
            id=scan.media_item.jellyfin_id,
            library_id=scan.media_item.library_id,
            name=name,
            item_type="Movie",
            path=scan.source_path,
            duration_seconds=60,
        )
        scan.duration_seconds = 60
        review = root.create_saved_review(
            db,
            media_item_id=scan.media_item_id,
            media_source_id=None,
            data=ReviewCreate(
                name=name, memberships=[ReviewScanMembership(scan_id=scan_id, role="video")]
            ),
            duration_seconds=60,
        )
        row = db.get(SavedReviewRow, review.id)
        row.owner_id = row.original_author_id = owner
        db.commit()
    return review


def test_own_rename_rejects_stale_revision(app):
    review = seeded_review(app, USER, "own")
    headers = app.accounts.unsafe_cookie(USER)
    renamed = app.send(
        "PATCH", f"/api/reviews/{review.id}", headers, {"name": "new", "revision": 0}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "new"
    stale = app.send("PATCH", f"/api/reviews/{review.id}", headers, {"name": "old", "revision": 0})
    assert stale.status_code == 409


def test_admin_copy_changes_owner_preserves_author_and_shared_evidence(app):
    review = seeded_review(app, PEER_USER, "source")
    response = app.send(
        "POST",
        f"/api/reviews/{review.id}/duplicate",
        app.accounts.unsafe_cookie(ADMIN),
        {"name": "copy", "revision": 0},
    )
    assert response.status_code == 201, response.text
    with app.accounts.factory() as db:
        copied = db.get(SavedReviewRow, response.json()["id"])
        assert copied.owner_id == ADMIN
        assert copied.original_author_id == PEER_USER
        assert [value.scan_id for value in copied.memberships] == [
            value.scan_id for value in db.get(SavedReviewRow, review.id).memberships
        ]
    assert (
        app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER)).status_code == 404
    )


def test_create_owns_review_and_list_excludes_foreign_reviews(app):
    item_id = app.jellyfin_item(guid("creation"), LIBRARY_A)
    path = f"/api/library-items/{item_id}/reviews"
    response = app.send("POST", path, app.accounts.unsafe_cookie(USER), {"name": "mine"})
    assert response.status_code == 201, response.text
    review_id = response.json()["id"]
    with app.accounts.factory() as db:
        stored = db.get(SavedReviewRow, review_id)
        assert stored.owner_id == stored.original_author_id == USER
    assert [row["id"] for row in app.send("GET", path, app.accounts.cookie(USER)).json()] == [
        review_id
    ]
    assert app.send("GET", path, app.accounts.cookie(PEER_USER)).json() == []


IMPORTS = {
    "edl": b"1 2 1\n",
    "mcf": (
        b"WEBVTT MovieContentFilter 1.1.0\n\nNOTE\nTITLE Movie\nYEAR 2000\nTYPE movie\n"
        b"\nNOTE\nSTART 00:00:00.000\nEND 00:01:00.000\n"
        b"\n00:00:01.000 --> 00:00:02.000\nviolence=high\n"
    ),
}


@pytest.mark.parametrize("kind", ["edl", "mcf"])
def test_uploaded_import_is_actor_owned(app, monkeypatch, kind):
    item_id = app.jellyfin_item(guid("import"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60.0)
    options = {"target": {"source": "jellyfin", "item_id": item_id}, "name": "upload"}
    if kind == "mcf":
        options["synchronization"] = {"start_seconds": 0, "end_seconds": 60, "end_mode": "file_end"}
        options["mcf_material_end_seconds"] = 60
    app.client.cookies.clear()
    response = app.client.post(
        f"/api/{kind}-imports/upload",
        headers=app.accounts.unsafe_cookie(USER),
        data={"options": json.dumps(options)},
        files={"file": (f"input.{kind}", IMPORTS[kind])},
    )
    assert response.status_code == 201, response.text
    imported = response.json()
    with app.accounts.factory() as db:
        review = db.get(SavedReviewRow, imported["review"]["id"])
        scan = db.get(ScanRow, imported["scan"]["id"])
        assert review.owner_id == review.original_author_id == USER
        assert scan.initiator_id == USER
    denied = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(PEER_USER))
    assert denied.status_code == 404


def save_body(review):
    return {
        field: review.model_dump(mode="json")[field]
        for field in (
            "revision",
            "name",
            "memberships",
            "filters",
            "mcf_document",
            "synchronization",
        )
    }


ACTIONS = [
    ("GET", "", None),
    ("PATCH", "", {"revision": 0, "name": "renamed"}),
    ("PUT", "", "save"),
    ("POST", "/duplicate", {"revision": 0, "name": "copy"}),
    ("DELETE", "?revision=0", None),
    ("POST", "/retime", "retime"),
    ("GET", "/filters/missing/mute-preview", None),
    ("GET", "/filters/missing/video-preview", None),
    ("POST", "/edl", {"revision": 0}),
    ("POST", "/mcf", {"revision": 0}),
    ("GET", "/mcf/preflight", None),
]


def action_body(review, body):
    if body == "save":
        return save_body(review)
    if body == "retime":
        return {
            "revision": 0,
            "memberships": [],
            "filters": [],
            "new_manual_filters": [],
            "current_synchronization": review.synchronization.model_dump(mode="json"),
            "proposed_synchronization": review.synchronization.model_dump(mode="json"),
            "mcf_document": review.mcf_document.model_dump(mode="json"),
            "accepted_credits": None,
        }
    return body


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("method,suffix,body", ACTIONS)
def test_all_hidden_actions_match_absent_ids(app, credential, method, suffix, body):
    review = seeded_review(app, PEER_USER, "private-action")
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    payload = action_body(review, body)
    hidden = app.send(method, f"/api/reviews/{review.id}{suffix}", headers, payload)
    absent = app.send(method, f"/api/reviews/absent{suffix}", headers, payload)
    assert hidden.status_code == absent.status_code == 404, hidden.text
    assert hidden.content == absent.content
    assert hidden.headers == absent.headers


@pytest.mark.parametrize("method,suffix,body", ACTIONS)
def test_admin_cross_user_actions_are_media_scoped(app, method, suffix, body):
    from tests.scan_access_http import LIBRARY_B

    review = seeded_review(app, PEER_USER, "restricted-action")
    app.limit_to_library(ADMIN, LIBRARY_B)
    response = app.send(
        method,
        f"/api/reviews/{review.id}{suffix}",
        app.accounts.unsafe_cookie(ADMIN),
        action_body(review, body),
    )
    assert response.status_code == 404, response.text


def test_own_save_rejects_foreign_media_and_keeps_revision(app):
    own = seeded_review(app, USER, "save")
    foreign = seeded_review(app, PEER_USER, "other-media")
    body = save_body(own)
    body["memberships"] = foreign.model_dump(mode="json")["memberships"]
    rejected = app.send("PUT", f"/api/reviews/{own.id}", app.accounts.unsafe_cookie(USER), body)
    assert rejected.status_code == 409, rejected.text
    current = app.send("GET", f"/api/reviews/{own.id}", app.accounts.cookie(USER))
    assert current.json()["revision"] == 0
    assert current.json()["memberships"] == own.model_dump(mode="json")["memberships"]


def test_save_noop_metadata_and_evidence_removal(app):
    own = seeded_review(app, USER, "save-edits")
    path = f"/api/reviews/{own.id}"
    body = save_body(own)
    noop = app.send("PUT", path, app.accounts.unsafe_cookie(USER), body)
    assert noop.status_code == 200, noop.text
    assert noop.json()["revision"] == 0
    body["mcf_document"]["title"] = "Edited metadata"
    body["memberships"] = []
    edited = app.send("PUT", path, app.accounts.unsafe_cookie(USER), body)
    assert edited.status_code == 200, edited.text
    assert edited.json()["mcf_document"]["title"] == "Edited metadata"
    assert edited.json()["memberships"] == []
    assert edited.json()["revision"] == 1


def test_repository_gate_reloads_permissions_before_mutating(app, monkeypatch):
    from scanner.persistence.repository import review_access

    review = seeded_review(app, USER, "revocation")
    original = review_access.gather_evidence
    changed = False

    def revoke(*args, **kwargs):
        nonlocal changed
        value = original(*args, **kwargs)
        if not changed:
            changed = True
            app.accounts.set_override(USER, C.REVIEWS_RENAME_OWN, OverrideEffect.DENY)
        return value

    monkeypatch.setattr(review_access, "gather_evidence", revoke)
    with app.accounts.factory() as db:
        caller = ReviewAccess(db, app.accounts.access(USER))
        from scanner.models import ReviewRename
        from scanner.security.access.decisions import AccessDenied

        with pytest.raises(AccessDenied):
            caller.run(
                review.id, RENAME, root.rename_saved_review, ReviewRename(revision=0, name="denied")
            )
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        assert row.revision == 0 and row.name == "revocation"


@pytest.mark.parametrize(
    "action", ["VIEW", "SAVE", "RENAME", "COPY", "DELETE", "PREVIEW", "TIMING", "CREDITS", "EXPORT"]
)
def test_repository_private_actions_never_invoke_an_operation_for_hidden_ids(app, action):
    from scanner.persistence.repository import review_access
    from scanner.security.access.api_shapes import AccessErrorCode
    from scanner.security.access.decisions import AccessDenied

    review = seeded_review(app, PEER_USER, "repository-private")
    calls = []
    with app.accounts.factory() as db:
        caller = ReviewAccess(db, app.accounts.access(USER))
        for review_id in [review.id, "absent"]:
            with pytest.raises(AccessDenied) as denied:
                caller.run(
                    review_id,
                    getattr(review_access, action),
                    lambda *_args: calls.append(review_id),
                )
            assert denied.value.code == AccessErrorCode.NOT_FOUND
    assert calls == []


def test_draft_preview_renders_evidence_only_for_its_target(app, monkeypatch):
    scan_id, _ = app.seed_scan(USER, "filter-evidence", status="complete")
    with app.accounts.factory() as db:
        scan = db.get(ScanRow, scan_id)
        path = scan.source_path
        item_id = scan.media_item.jellyfin_id
        scan.duration_seconds = 60
        scan.media_source_id = "theatrical"
        evidence = ScanFindingRow(
            scan_id=scan_id, source="visual", start_seconds=10, end_seconds=18
        )
        db.add(evidence)
        db.commit()
        finding_id = evidence.id
    app.items[item_id] = JellyfinMediaItem(
        id=item_id,
        library_id=LIBRARY_A,
        name="Evidence film",
        item_type="Movie",
        path=path,
        duration_seconds=60,
        versions=[
            JellyfinMediaVersion(id="theatrical", path=path),
            JellyfinMediaVersion(id="extended", path=path),
        ],
    )
    rendered = []

    def render(media, start, end):
        rendered.append((str(media), start, end))
        return b"preview video"

    monkeypatch.setattr(repository, "render_video_preview", render)
    body = {
        "media_source_id": "theatrical",
        "source_scan_id": scan_id,
        "source_finding_id": finding_id,
        "source": "visual",
        "edit_action": "skip",
        "start_seconds": 10,
        "end_seconds": 18,
    }
    app.accounts.set_override(USER, C.REVIEWS_CUES_EDIT_OWN, OverrideEffect.DENY)
    headers = app.accounts.unsafe_cookie(USER)
    route = f"/api/library-items/{item_id}/reviews/preview"
    response = app.send("POST", route, headers, body)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == b"preview video"
    assert rendered == [(path, 10, 18)]

    denied = app.send("POST", route, headers, {**body, "start_seconds": 11})
    assert denied.status_code == 403, denied.text
    other = app.jellyfin_item(guid("different-preview-item"), LIBRARY_A)
    wrong_item = app.send(
        "POST",
        f"/api/library-items/{other}/reviews/preview",
        headers,
        {**body, "media_source_id": None},
    )
    assert wrong_item.status_code == 404, wrong_item.text
    wrong_version = app.send("POST", route, headers, {**body, "media_source_id": "extended"})
    assert wrong_version.status_code == 404, wrong_version.text
    assert rendered == [(path, 10, 18)]
    with app.accounts.factory() as db:
        assert db.query(SavedReviewRow).count() == 0


def test_draft_preview_uses_unsaved_timing_without_storing_it(app, monkeypatch):
    review = seeded_review(app, PEER_USER, "timing-preview")
    route = f"/api/library-items/{review.target.item_id}/reviews/preview"
    rendered = []

    def render(media, start, end):
        rendered.append((str(media), start, end))
        return b"preview audio"

    monkeypatch.setattr(repository, "render_mute_preview", render)
    body = {"source": "audio", "edit_action": "mute", "start_seconds": 11.5, "end_seconds": 16.5}
    app.accounts.set_override(USER, C.REVIEWS_PREVIEW_OWN, OverrideEffect.DENY)
    denied = app.send("POST", route, app.accounts.unsafe_cookie(USER), body)
    assert denied.status_code == 403, denied.text
    foreign = app.send(
        "POST", route, app.accounts.unsafe_cookie(USER), {**body, "review_id": review.id}
    )
    assert foreign.status_code == 404, foreign.text
    assert rendered == []

    unsaved = app.send("POST", route, app.accounts.unsafe_cookie(PEER_USER), body)
    assert unsaved.status_code == 200, unsaved.text
    assert unsaved.content == b"preview audio"

    # An administrator can preview an authorized other-owner draft using the
    # current cue values, without creating a new review or a manual scan.
    response = app.send(
        "POST", route, app.accounts.unsafe_cookie(ADMIN), {**body, "review_id": review.id}
    )
    assert response.status_code == 200, response.text
    assert response.content == b"preview audio"
    assert response.headers["content-type"] == "audio/mpeg"
    assert rendered == [(app.items[review.target.item_id].path, 11.5, 16.5)] * 2

    with app.accounts.factory() as db:
        assert db.query(SavedReviewRow).count() == 1
        assert db.query(ScanRow).count() == 1
        assert db.get(SavedReviewRow, review.id).revision == review.revision
