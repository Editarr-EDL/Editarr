"""Review credits, path projection, publication provenance, and independent permissions."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from scanner import repository
from scanner.credits.credits_detector import (
    ChapterMarker,
    default_configuration,
    detect_from_observations,
)
from scanner.db import MediaItemExportRow, SavedReviewRow, ScanFindingRow, ScanRow
from scanner.models import ReviewCreate, ScanMode
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.persistence.repository.review_access import ReviewAccess, media_facts
from scanner.persistence.repository.review_deletion import preview_review_deletion
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.scan_access_http import LIBRARY_A, LIBRARY_B, guid
from tests.test_review_access_http import IMPORTS, seeded_review
from tests.test_review_access_http import app as app


def credits_scan(app, review):
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        media = row.media_item
        configuration = default_configuration(qwen_model=None)
        scan = repository.create_scan(
            db,
            media_item=media,
            resolved_local_path=media.path,
            source_path=media.path,
            name="credits",
            duration_seconds=60,
            scan_audio=False,
            audio_model=None,
            audio_filter=None,
            scan_video=True,
            video_model=None,
            scan_mode=ScanMode.CREDITS_DETECTION,
            credits_detection_configuration=configuration,
            initiator=ActivityOwner(PEER_USER, app.revision(PEER_USER)),
        )
        result = detect_from_observations(
            duration_seconds=60,
            configuration=configuration,
            chapters=[ChapterMarker(50, "End Credits")],
        )
        assert result.outcome == "candidate"
        repository.complete_credits_detection(
            db, scan_id=scan.id, cache_key="b" * 64, result=result
        )
        return scan.id


def test_shared_credits_acceptance_cannot_address_private_review(app, monkeypatch):
    review = seeded_review(app, USER, "credits-review")
    scan_id = credits_scan(app, review)
    body = {
        "context": {"kind": "review", "review_id": review.id, "revision": 0},
        "accepted_boundary_seconds": 51,
    }
    hidden = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(PEER_USER),
        body,
    )
    absent = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(PEER_USER),
        body | {"context": body["context"] | {"review_id": "absent"}},
    )
    assert hidden.status_code == absent.status_code == 404
    assert hidden.content == absent.content and hidden.headers == absent.headers
    accepted = app.send(
        "POST", f"/api/credits-detections/{scan_id}/accept", app.accounts.unsafe_cookie(USER), body
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["filter"]["start_seconds"] == 51
    with app.accounts.factory() as db:
        finding = db.query(ScanFindingRow).filter_by(scan_id=scan_id).one()
        assert finding.start_seconds == 50
        assert db.get(SavedReviewRow, review.id).revision == 0
    stale = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(USER),
        body | {"context": body["context"] | {"revision": 10}},
    )
    assert stale.status_code == 409
    monkeypatch.setattr(repository, "render_video_preview", lambda *_args: b"verified-mp4")
    clip = app.send(
        "GET",
        f"/api/credits-detections/{scan_id}/clip?boundary_seconds=51",
        app.accounts.cookie(PEER_USER),
    )
    assert clip.status_code == 200 and clip.content == b"verified-mp4"
    app.limit_to_library(PEER_USER, LIBRARY_B)
    refused = app.send(
        "GET",
        f"/api/credits-detections/{scan_id}/clip?boundary_seconds=51",
        app.accounts.cookie(PEER_USER),
    )
    assert refused.status_code == 404


def test_published_review_deletion_preserves_file_and_provenance(app):
    review = seeded_review(app, USER, "published")
    published = app.send(
        "POST", f"/api/reviews/{review.id}/edl", app.accounts.unsafe_cookie(USER), {"revision": 0}
    )
    assert published.status_code == 200, published.text
    with app.accounts.factory() as db:
        export = db.query(MediaItemExportRow).filter_by(review_id=review.id).one()
        sidecar = Path(export.destination_path)
    original = sidecar.read_bytes()
    with app.accounts.factory() as db:
        impact = preview_review_deletion(ReviewAccess(db, app.accounts.access(USER)), review.id)
        ((published_file, choices),) = [(f["publication"], f["choices"]) for f in impact["files"]]
        assert choices == ["remove", "retain"]
        assert published_file.policy_owner_id == published_file.original_author_id == USER
        assert published_file.format == "edl"
        assert published_file.target_user_id == USER
    rejected = app.send(
        "DELETE", f"/api/reviews/{review.id}?revision=0", app.accounts.unsafe_cookie(USER)
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "published_review_retention_required"
    assert sidecar.read_bytes() == original
    with app.accounts.factory() as db:
        export = db.query(MediaItemExportRow).filter_by(review_id=review.id).one()
        assert export.exported_format_revision == 0
        assert db.get(SavedReviewRow, review.id).revision == 0


def test_admin_can_delete_unpublished_review_without_deleting_shared_scans(app):
    review = seeded_review(app, USER, "delete-me")
    response = app.send(
        "DELETE", f"/api/reviews/{review.id}?revision=0", app.accounts.unsafe_cookie(ADMIN)
    )
    assert response.status_code == 204, response.text
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id) is None
        assert db.get(ScanRow, review.memberships[0].scan_id) is not None


def test_own_review_does_not_disclose_foreign_published_review(app):
    review = seeded_review(app, PEER_USER, "private-export")
    published = app.send(
        "POST",
        f"/api/reviews/{review.id}/edl",
        app.accounts.unsafe_cookie(PEER_USER),
        {"revision": 0},
    )
    assert published.status_code == 200, published.text
    path = f"/api/library-items/{guid('private-export')}/reviews"
    created = app.send("POST", path, app.accounts.unsafe_cookie(USER), {"name": "mine"})
    assert created.status_code == 201, created.text
    assert created.json()["exports"]["exports"]["edl"]["review_id"] is None
    assert review.id not in created.text
    listed = app.send("GET", path, app.accounts.cookie(USER))
    assert listed.status_code == 200, listed.text
    assert review.id not in listed.text


def test_review_ids_and_nested_scans_do_not_disclose_paths(app, monkeypatch):
    app.jellyfin_item(guid("private-path"), LIBRARY_A)
    scan_id, _queue = app.seed_scan(USER, "private-path", status="complete")
    with app.accounts.factory() as db:
        scan = db.get(ScanRow, scan_id)
        scan.duration_seconds = 60
        path = scan.source_path
        review = ReviewAccess(db, app.accounts.access(USER)).create(
            media_facts(db, scan.media_item),
            lambda session: repository.create_saved_review(
                session,
                media_item_id=scan.media_item_id,
                media_source_id=None,
                data=ReviewCreate(
                    name="private path", memberships=[{"scan_id": scan_id, "role": "video"}]
                ),
                duration_seconds=60,
            ),
            scan_ids=[scan_id],
        )
    detail = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER))
    assert detail.status_code == 200, detail.text
    assert path not in detail.text
    assert detail.json()["target"]["source"] == "jellyfin"
    item_id = detail.json()["target"]["item_id"]
    assert item_id == guid("private-path")
    listed = app.send(
        "GET", f"/api/library-items/{item_id}/reviews", app.accounts.cookie(USER)
    )
    assert listed.status_code == 200, listed.text
    assert [value["id"] for value in listed.json()] == [review.id]
    assert path not in listed.text


def test_cue_edit_does_not_require_filter_selection_permission(app, monkeypatch):
    import scanner.api as root

    item_id = app.jellyfin_item(guid("cue-edit"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60)
    app.client.cookies.clear()
    response = app.client.post(
        "/api/edl-imports/upload",
        headers=app.accounts.unsafe_cookie(USER),
        data={
            "options": json.dumps(
                {"target": {"source": "jellyfin", "item_id": item_id}, "name": "cue"}
            )
        },
        files={"file": ("cue.edl", IMPORTS["edl"])},
    )
    assert response.status_code == 201, response.text
    review_id = response.json()["review"]["id"]
    body = {
        key: response.json()["review"][key]
        for key in (
            "revision",
            "name",
            "memberships",
            "filters",
            "mcf_document",
            "synchronization",
            "policy_owner_id",
            "policy_revision",
        )
    }
    app.accounts.set_override(USER, C.REVIEWS_FILTERS_EDIT_OWN, OverrideEffect.DENY)
    body["filters"][0]["mcf_comment"] = "Edited cue"
    edited = app.send("PUT", f"/api/reviews/{review_id}", app.accounts.unsafe_cookie(USER), body)
    assert edited.status_code == 200, edited.text
    body["revision"] = 1
    body["filters"][0]["selected"] = False
    refused = app.send("PUT", f"/api/reviews/{review_id}", app.accounts.unsafe_cookie(USER), body)
    assert refused.status_code == 403, refused.text


def test_catalog_provider_is_never_called_inside_review_write_lock(app, monkeypatch):
    import scanner.api as root
    from scanner.persistence.repository import review_access

    review = seeded_review(app, USER, "network-order")
    locked = False
    original_lock = review_access.write_lock
    original_item = root.JellyfinClient.item
    calls = []

    @contextmanager
    def watched_lock(db):
        nonlocal locked
        with original_lock(db):
            assert db.connection().connection.driver_connection.in_transaction
            locked = True
            try:
                yield
            finally:
                locked = False

    def watched_item(item_id):
        assert not locked
        calls.append(item_id)
        return original_item(item_id)

    monkeypatch.setattr(review_access, "write_lock", watched_lock)
    monkeypatch.setattr(root.JellyfinClient, "item", watched_item)
    copied = app.send(
        "POST",
        f"/api/reviews/{review.id}/duplicate",
        app.accounts.unsafe_cookie(ADMIN),
        {"revision": 0, "name": "copy"},
    )
    assert copied.status_code == 201, copied.text
    assert calls


def test_read_gate_releases_its_writer_transaction_before_projection(app):
    from scanner.persistence.repository.review_access import VIEW

    review = seeded_review(app, USER, "read-transaction")
    with app.accounts.factory() as db:
        caller = ReviewAccess(db, app.accounts.access(USER))
        result = caller.run(review.id, VIEW, repository.get_saved_review)
        assert result.id == review.id
        assert not db.connection().connection.driver_connection.in_transaction


@pytest.mark.parametrize("kind", ["edl", "mcf"])
def test_admin_import_credits_the_actor(app, monkeypatch, kind):
    import scanner.api as root

    item_id = app.jellyfin_item(guid("admin-import"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60)
    body = {"target": {"source": "jellyfin", "item_id": item_id}, "name": "admin import"}
    if kind == "mcf":
        body.update(
            synchronization={"start_seconds": 0, "end_seconds": 60, "end_mode": "file_end"},
            mcf_material_end_seconds=60,
        )
    app.client.cookies.clear()
    imported = app.client.post(
        f"/api/{kind}-imports/upload",
        headers=app.accounts.unsafe_cookie(ADMIN),
        data={"options": json.dumps(body)},
        files={"file": (f"input.{kind}", IMPORTS[kind])},
    )
    assert imported.status_code == 201, imported.text
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, imported.json()["review"]["id"])
        assert row.owner_id == row.original_author_id == ADMIN
        assert db.get(ScanRow, imported.json()["scan"]["id"]).initiator_id == ADMIN


def test_own_imported_filter_preview_uses_authorized_evidence(app, monkeypatch):
    import scanner.api as root

    item_id = app.jellyfin_item(guid("mute-preview"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60)
    monkeypatch.setattr(repository, "render_mute_preview", lambda *_args: b"authorized-audio")
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
    route = f"/api/reviews/{review['id']}/filters/{review['filters'][0]['id']}/mute-preview"
    own = app.send("GET", route, app.accounts.cookie(USER))
    assert own.status_code == 200 and own.content == b"authorized-audio"
    assert own.headers["cache-control"] == "no-store"
    other = app.send("GET", route, app.accounts.cookie(PEER_USER))
    assert other.status_code == 404


def test_credits_rejects_another_physical_media_target(app):
    review = seeded_review(app, USER, "credits-one")
    other = seeded_review(app, USER, "credits-two")
    scan_id = credits_scan(app, other)
    response = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(USER),
        {
            "context": {"kind": "review", "review_id": review.id, "revision": 0},
            "accepted_boundary_seconds": 51,
        },
    )
    assert response.status_code == 409, response.text
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id).revision == 0


@pytest.mark.parametrize("owner", [USER, ADMIN])
def test_mcf_import_cannot_bypass_denied_credits_edit(app, monkeypatch, owner):
    import scanner.api as root

    source = seeded_review(app, owner, "credits-import-bypass")
    scan_id = credits_scan(app, source)
    request = {
        "context": {"kind": "review", "review_id": source.id, "revision": 0},
        "accepted_boundary_seconds": 51,
    }
    accepted = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(owner),
        request,
    )
    assert accepted.status_code == 200, accepted.text
    reference = accepted.json()["accepted_credits"]
    app.accounts.set_override(owner, C.REVIEWS_CREDITS_EDIT_OWN, OverrideEffect.DENY)
    denied = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(owner),
        request,
    )
    assert denied.status_code == 403, denied.text
    options = {
        "name": "denied credits",
        "target": {"source": "jellyfin", "item_id": guid("credits-import-bypass")},
        "synchronization": {"start_seconds": 0, "end_seconds": 60, "end_mode": "file_end"},
        "mcf_material_end_seconds": 60,
        "accepted_credits": reference,
    }
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60.0)
    with app.accounts.factory() as db:
        before = (db.query(SavedReviewRow).count(), db.query(ScanRow).count())
    app.client.cookies.clear()
    imported = app.client.post(
        "/api/mcf-imports/upload",
        headers=app.accounts.unsafe_cookie(owner),
        data={"options": json.dumps(options)},
        files={"file": ("credits.mcf", IMPORTS["mcf"])},
    )
    assert imported.status_code == 403, imported.text
    assert imported.json()["code"] == "capability_denied"
    with app.accounts.factory() as db:
        assert (db.query(SavedReviewRow).count(), db.query(ScanRow).count()) == before
        assert db.get(SavedReviewRow, source.id).revision == 0
