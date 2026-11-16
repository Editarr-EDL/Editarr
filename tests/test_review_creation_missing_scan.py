"""Unavailable review source scans never disclose their existence."""

import pytest

from scanner.db import (
    MediaAccessScopeRow,
    MediaAccessScopeSourceRow,
    SavedReviewRow,
    ScanRow,
    UserRow,
)
from scanner.models import JellyfinMediaItem
from tests.multi_user_access_support import PEER_USER, USER
from tests.scan_access_http import LIBRARY_A, LIBRARY_B, guid
from tests.test_review_access_http import app as app


def _item_for(app, scan_id):
    with app.accounts.factory() as db:
        scan = db.get(ScanRow, scan_id)
        item_id = scan.media_item.jellyfin_id
        app.items[item_id] = JellyfinMediaItem(
            id=item_id,
            library_id=scan.media_item.library_id,
            name="Shared film",
            item_type="Movie",
            path=scan.source_path,
            duration_seconds=60,
        )
        scan.duration_seconds = 60
        db.commit()
    return item_id


def test_create_review_after_selected_scan_deleted(app):
    kept, _ = app.seed_scan(USER, "shared-film", status="complete")
    item_id = _item_for(app, kept)
    path = f"/api/library-items/{item_id}/reviews"
    headers = app.accounts.unsafe_cookie(USER)
    control = app.send(
        "POST",
        path,
        headers,
        {"name": "control", "memberships": [{"scan_id": kept, "role": "video"}]},
    )
    assert control.status_code == 201, control.text
    review = control.json()
    assert (
        app.send(
            "DELETE", f"/api/reviews/{review['id']}?revision={review['revision']}", headers
        ).status_code
        == 204
    )
    assert app.send("DELETE", f"/api/scans/{kept}", headers).is_success
    after = app.send(
        "POST",
        path,
        headers,
        {"name": "after delete", "memberships": [{"scan_id": kept, "role": "video"}]},
    )
    peer, _ = app.seed_scan(PEER_USER, "shared-film", status="complete")
    with app.accounts.factory() as db:
        db.get(ScanRow, peer).duration_seconds = 60
        db.commit()
    visible = app.send("GET", "/api/scans/history", app.accounts.cookie(USER)).json()
    assert any(scan["id"] == peer for scan in visible)
    assert app.send(
        "DELETE", f"/api/scans/{peer}", app.accounts.unsafe_cookie(PEER_USER)
    ).is_success
    peer_after = app.send(
        "POST",
        path,
        headers,
        {"name": "after peer delete", "memberships": [{"scan_id": peer, "role": "video"}]},
    )
    assert app.send("GET", path, app.accounts.cookie(USER)).status_code == 200
    for response in (after, peer_after):
        assert response.status_code == 404, response.text
        body = response.json()
        detail = body.get("detail")
        code = body.get("code") or (detail.get("code") if isinstance(detail, dict) else None)
        assert code == "scan_not_found", body


def test_create_review_rechecks_selected_scan_under_write_fence(app, monkeypatch):
    from scanner.persistence.repository import review_access

    scan_id, _ = app.seed_scan(USER, "race-film", status="complete")
    item_id = _item_for(app, scan_id)
    original = review_access.scan_job_facts
    reads = 0

    def read_scans(db, scan_ids):
        nonlocal reads
        reads += 1
        return {} if reads == 2 else original(db, scan_ids)

    monkeypatch.setattr(review_access, "scan_job_facts", read_scans)
    route = f"/api/library-items/{item_id}/reviews"
    response = app.send(
        "POST",
        route,
        app.accounts.unsafe_cookie(USER),
        {"name": "Race draft", "memberships": [{"scan_id": scan_id, "role": "video"}]},
    )
    assert reads == 2
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["code"] == "scan_not_found"
    assert app.send("GET", route, app.accounts.cookie(USER)).json() == []


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("state,library", [("complete", LIBRARY_B), ("queued", LIBRARY_A)])
def test_hidden_and_absent_memberships_have_identical_http_responses(
    app, credential, state, library
):
    target = app.jellyfin_item(guid("accessible-destination"), LIBRARY_A)
    hidden_id, _ = app.seed_scan(PEER_USER, "hidden-source", status=state, library=library)
    app.limit_to_library(USER, LIBRARY_A)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    hidden_read = app.send("GET", f"/api/scans/{hidden_id}/findings", headers)
    missing_read = app.send("GET", "/api/scans/never-created/findings", headers)
    assert hidden_read.status_code == missing_read.status_code == 404
    assert hidden_read.content == missing_read.content
    responses = [
        app.send(
            "POST",
            f"/api/library-items/{target}/reviews",
            headers,
            {
                "name": "Private source probe",
                "memberships": [{"scan_id": scan_id, "role": "video"}],
            },
        )
        for scan_id in [hidden_id, "never-created"]
    ]
    hidden, absent = responses
    assert app.send("GET", f"/api/library-items/{target}/reviews", headers).json() == []
    assert hidden.status_code == absent.status_code == 404
    assert hidden.content == absent.content
    assert hidden.headers == absent.headers
    assert absent.json()["detail"]["code"] == "scan_not_found"


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("phase", ["initial", "fence"])
@pytest.mark.parametrize("state", ["absent", "hidden", "unusable"])
def test_unavailable_memberships_match_absent_at_each_check(
    app, monkeypatch, credential, phase, state
):
    from scanner.persistence.repository import review_access

    target = app.jellyfin_item(guid("fenced-destination"), LIBRARY_A)
    app.limit_to_library(USER, LIBRARY_A)
    source_id, _ = app.seed_scan(USER, "fenced-source", status="complete")
    route = f"/api/library-items/{target}/reviews"
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )

    def create(scan_id):
        return app.send(
            "POST",
            route,
            headers,
            {"name": "Fenced draft", "memberships": [{"scan_id": scan_id, "role": "video"}]},
        )

    absent = create("never-created")
    original = review_access.scan_job_facts
    reads = 0

    def read_scans(db, scan_ids):
        nonlocal reads
        reads += 1
        if reads == (1 if phase == "initial" else 2):
            if state == "absent":
                return {}
            row = db.get(ScanRow, source_id)
            if state == "hidden":
                row.media_item.library_id = LIBRARY_B
            else:
                row.status = "queued"
            db.flush()
        return original(db, scan_ids)

    monkeypatch.setattr(review_access, "scan_job_facts", read_scans)
    response = create(source_id)
    assert reads == (1 if phase == "initial" and state == "absent" else 2)
    assert response.status_code == absent.status_code == 404
    assert response.content == absent.content
    assert response.headers == absent.headers
    assert response.json()["detail"]["code"] == "scan_not_found"
    assert app.send("GET", route, headers).json() == []
    assert app.scan(source_id).status == "complete"


@pytest.mark.parametrize("credential", ["session", "key"])
def test_destination_media_refusals_keep_their_original_response(app, credential):
    denied = app.jellyfin_item(guid("denied-destination"), LIBRARY_B)
    source_id, _ = app.seed_scan(USER, "destination-source", status="complete")
    app.limit_to_library(USER, LIBRARY_A)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    route = f"/api/library-items/{denied}/reviews"
    empty = app.send("POST", route, headers, {"name": "Denied draft"})
    membership = app.send(
        "POST",
        route,
        headers,
        {"name": "Denied draft", "memberships": [{"scan_id": source_id, "role": "video"}]},
    )
    assert empty.status_code == membership.status_code == 404
    assert empty.content == membership.content
    assert empty.headers == membership.headers
    assert empty.json()["code"] == "not_found"


def test_default_repository_create_keeps_the_original_membership_denial(app, monkeypatch):
    from scanner.persistence.repository import review_access
    from scanner.security.access.api_shapes import AccessErrorCode
    from scanner.security.access.decisions import AccessDenied

    scan_id, _ = app.seed_scan(USER, "default-caller", status="complete")
    denial = AccessDenied(AccessErrorCode.CAPABILITY_DENIED)

    def refuse(*args, **kwargs):
        raise denial

    monkeypatch.setattr(review_access.ReviewAccess, "require_scan", refuse)
    called = []
    with app.accounts.factory() as db:
        media = review_access.media_facts(db, db.get(ScanRow, scan_id).media_item)
        caller = review_access.ReviewAccess(db, app.accounts.access(USER))
        with pytest.raises(AccessDenied) as caught:
            caller.create(media, lambda session: called.append(session), scan_ids=[scan_id])
        assert caught.value is denial
    assert called == []


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("state", ["complete", "queued"])
def test_denied_destination_conceals_membership_existence(app, credential, state):
    target = app.jellyfin_item(guid("review4b-denied"), LIBRARY_B)
    scan_id, _ = app.seed_scan(PEER_USER, "review4b-hidden", status=state, library=LIBRARY_B)
    app.limit_to_library(USER, LIBRARY_A)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    existing, absent = [
        app.send(
            "POST",
            f"/api/library-items/{target}/reviews",
            headers,
            {"name": "Existence probe", "memberships": [{"scan_id": member, "role": "video"}]},
        )
        for member in [scan_id, "never-created"]
    ]
    with app.accounts.factory() as db:
        assert db.query(SavedReviewRow).count() == 0
    assert existing.status_code == absent.status_code == 404
    assert (existing.content, existing.headers) == (absent.content, absent.headers)


@pytest.mark.parametrize("credential", ["session", "key"])
def test_peer_private_queue_at_write_fence_matches_absent(app, monkeypatch, credential):
    from scanner.persistence.repository import review_access

    target = app.jellyfin_item(guid("review4b-visible"), LIBRARY_A)
    scan_id, _ = app.seed_scan(PEER_USER, "review4b-fenced", status="complete", library=LIBRARY_A)
    app.limit_to_library(USER, LIBRARY_A)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )

    def create(member):
        return app.send(
            "POST",
            f"/api/library-items/{target}/reviews",
            headers,
            {"name": "Fenced probe", "memberships": [{"scan_id": member, "role": "video"}]},
        )

    absent = create("never-created")
    original = review_access.scan_job_facts
    reads = 0

    def queue_at_fence(db, scan_ids):
        nonlocal reads
        reads += 1
        if reads == 2:
            db.get(ScanRow, scan_id).status = "queued"
            db.flush()
        return original(db, scan_ids)

    monkeypatch.setattr(review_access, "scan_job_facts", queue_at_fence)
    response = create(scan_id)
    assert reads == 2
    assert response.status_code == absent.status_code == 404
    assert (response.content, response.headers) == (absent.content, absent.headers)
    assert response.json()["detail"]["code"] == "scan_not_found"
    assert app.scan(scan_id).status == "complete"
    with app.accounts.factory() as db:
        assert db.query(SavedReviewRow).count() == 0


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("destination", ["allowed", "denied"])
@pytest.mark.parametrize("membership", ["absent", "hidden", "unusable", "valid"])
def test_destination_authorization_precedes_membership_errors(
    app, credential, destination, membership
):
    source_id, _ = app.seed_scan(USER, "matrix-source", status="complete")
    target = _item_for(app, source_id)
    if destination == "denied":
        target = app.jellyfin_item(guid("matrix-denied"), LIBRARY_B)
    if membership == "absent":
        source_id = "never-created"
    elif membership == "hidden":
        source_id, _ = app.seed_scan(PEER_USER, "matrix-hidden", library=LIBRARY_B)
    elif membership == "unusable":
        with app.accounts.factory() as db:
            db.get(ScanRow, source_id).status = "queued"
            db.commit()
    app.limit_to_library(USER, LIBRARY_A)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    route = f"/api/library-items/{target}/reviews"
    control = app.send("POST", route, headers, {"name": "Destination control"})
    response = app.send(
        "POST",
        route,
        headers,
        {"name": "Membership matrix", "memberships": [{"scan_id": source_id, "role": "video"}]},
    )
    if destination == "denied":
        assert response.status_code == control.status_code == 404
        assert (response.content, response.headers) == (control.content, control.headers)
        assert response.json()["code"] == "not_found"
    elif membership == "valid":
        assert response.status_code == 201, response.text
    else:
        absent = app.send(
            "POST",
            route,
            headers,
            {
                "name": "Absent control",
                "memberships": [{"scan_id": "never-created", "role": "video"}],
            },
        )
        assert response.status_code == absent.status_code == 404
        assert (response.content, response.headers) == (absent.content, absent.headers)
        assert response.json()["detail"]["code"] == "scan_not_found"
    with app.accounts.factory() as db:
        expected = int(control.status_code == 201) + int(response.status_code == 201)
        assert db.query(SavedReviewRow).count() == expected


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("revocation", ["scope", "capability"])
def test_membership_authority_revoked_during_capture_matches_absent(
    app, monkeypatch, credential, revocation
):
    from scanner.persistence.repository import review_access
    from scanner.security.access.capability_ids import Capability
    from scanner.security.access.overrides import OverrideEffect

    target = app.jellyfin_item(guid("authority-destination"), LIBRARY_A)
    scan_id, _ = app.seed_scan(USER, "authority-source", status="complete", library=LIBRARY_B)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    route = f"/api/library-items/{target}/reviews"

    def create(member):
        return app.send(
            "POST",
            route,
            headers,
            {"name": "Authority draft", "memberships": [{"scan_id": member, "role": "video"}]},
        )

    absent = create("never-created")
    original = review_access.gather_evidence
    changed = False

    def revoke(db, account, media, now):
        nonlocal changed
        evidence = original(db, account, media, now)
        if media.library_id == LIBRARY_B and not changed:
            changed = True
            if revocation == "scope":
                app.limit_to_library(USER, LIBRARY_A)
            else:
                app.accounts.set_override(USER, Capability.SCANS_FINDINGS_READ, OverrideEffect.DENY)
        return evidence

    monkeypatch.setattr(review_access, "gather_evidence", revoke)
    response = create(scan_id)
    assert changed
    assert response.status_code == absent.status_code == 404
    assert response.content == absent.content
    assert response.headers == absent.headers
    assert app.send("GET", route, headers).json() == []


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("membership", ["absent", "hidden", "unusable", "valid"])
def test_destination_revoked_during_capture_conceals_membership(
    app, monkeypatch, credential, membership
):
    from scanner.persistence.repository import review_access

    target = app.jellyfin_item(guid("destination-revoked"), LIBRARY_A)
    scan_id, _ = app.seed_scan(USER, "revoked-source", status="complete", library=LIBRARY_A)
    if membership == "absent":
        scan_id = "never-created"
    elif membership == "hidden":
        scan_id, _ = app.seed_scan(PEER_USER, "revoked-hidden", library=LIBRARY_B)
    elif membership == "unusable":
        with app.accounts.factory() as db:
            db.get(ScanRow, scan_id).status = "queued"
            db.commit()
    app.limit_to_library(USER, LIBRARY_A)
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    original = review_access.gather_evidence
    changed = False

    def revoke(db, account, media, now):
        nonlocal changed
        evidence = original(db, account, media, now)
        if media.library_id == LIBRARY_A and not changed:
            changed = True
            with app.accounts.factory() as session:
                source = (
                    session.query(MediaAccessScopeSourceRow)
                    .join(MediaAccessScopeRow)
                    .filter(MediaAccessScopeRow.user_id == USER)
                    .one()
                )
                source.source_id = LIBRARY_B
                session.get(UserRow, USER).permission_revision += 1
                session.commit()
        return evidence

    monkeypatch.setattr(review_access, "gather_evidence", revoke)
    route = f"/api/library-items/{target}/reviews"
    response = app.send(
        "POST",
        route,
        headers,
        {"name": "Revoked draft", "memberships": [{"scan_id": scan_id, "role": "video"}]},
    )
    control = app.send("POST", route, headers, {"name": "Denied destination"})
    assert changed
    assert response.status_code == control.status_code == 404
    assert (response.content, response.headers) == (control.content, control.headers)
    assert response.json()["code"] == "not_found"
    with app.accounts.factory() as db:
        assert db.query(SavedReviewRow).count() == 0
