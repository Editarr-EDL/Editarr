"""Scan jobs, history, and deletion decided per account over real HTTP (MU-21).

Users manage only their own jobs; admins manage others' within their media;
completed scans stay readable by everyone who may reach their media; deletion
counts every review reference but lists only the ones the caller may view.
"""

from __future__ import annotations

import pytest

import scanner.api as root
from scanner.db import ArchivedSource, JellyfinSettingsRow, MediaItemRow
from scanner.integrations.jellyfin import JellyfinNotFound
from scanner.persistence.archive import archived_reads
from scanner.repository import record_jellyfin_connection
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import (
    ADMIN,
    OWNER,
    PEER_ADMIN,
    PEER_USER,
    USER,
    live_accounts,
)
from tests.scan_access_http import LIBRARY_A, LIBRARY_B, serve_scans

CREDENTIALS = ("session", "key")


@pytest.fixture
def app(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    served = serve_scans(monkeypatch, accounts, tmp_path)
    yield served
    served.client.close()
    accounts.close()


def _headers(app, user_id: int, credential: str, *, unsafe: bool = False) -> dict:
    if credential == "key":
        return app.accounts.key(user_id)
    return app.accounts.unsafe_cookie(user_id) if unsafe else app.accounts.cookie(user_id)


def _queue(app, user_id: int, credential: str = "session") -> list[str]:
    response = app.send("GET", "/api/scans/queue", _headers(app, user_id, credential))
    assert response.status_code == 200, response.text
    return [item["name"] for item in response.json()]


def test_scan_options_follow_the_current_scan_start_permission(app):
    from scanner.http.scan_catalog_api import router

    app.client.app.include_router(router)
    permitted = app.send("GET", "/api/capabilities", app.accounts.cookie(USER))
    assert permitted.status_code == 200, permitted.text
    assert "audio_models" in permitted.json()
    assert "video_models" in permitted.json()
    app.accounts.set_override(USER, C.SCANS_START, OverrideEffect.DENY)
    refused = app.send("GET", "/api/capabilities", app.accounts.cookie(USER))
    assert refused.status_code == 403
    assert refused.json()["code"] == "capability_denied"
    assert app.send("GET", "/api/mcf-registry", app.accounts.cookie(USER)).status_code == 200


@pytest.mark.parametrize("credential", CREDENTIALS)
def test_users_see_and_manage_only_their_own_jobs(app, credential):
    own, own_queue = app.seed_scan(USER, "user-job")
    other, other_queue = app.seed_scan(PEER_USER, "peer-job")
    app.seed_scan(OWNER, "owner-job")

    assert _queue(app, USER, credential) == ["user-job"]
    assert set(_queue(app, ADMIN, credential)) == {"user-job", "peer-job", "owner-job"}
    own_view = app.send("GET", "/api/scans/queue", _headers(app, USER, credential)).json()
    assert own_view[0]["is_own"] is True
    admin_view = app.send("GET", "/api/scans/queue", _headers(app, ADMIN, credential)).json()
    assert all(scan["is_own"] is False for scan in admin_view)

    headers = _headers(app, USER, credential, unsafe=True)
    refused = app.send("DELETE", f"/api/scans/queue/{other_queue}", headers)
    assert refused.status_code == 404
    assert refused.json() == {"detail": "Queued scan not found"}
    assert app.scan(other).status == "queued"
    with app.accounts.factory() as db:
        db.get(type(app.scan(other)), other).status = "orphaned"
        db.commit()
    resumed = app.send("POST", f"/api/scans/queue/{other_queue}/resume", headers)
    assert resumed.status_code == 404
    assert app.scan(other).status == "orphaned"
    assert "wake" not in app.calls

    cancelled = app.send("DELETE", f"/api/scans/queue/{own_queue}", headers)
    assert cancelled.status_code == 200, cancelled.text
    assert app.scan(own).status == "cancelled"
    assert app.queued() == [other, *[s for s in app.queued() if s != other]][: len(app.queued())]


@pytest.mark.parametrize("credential", CREDENTIALS)
def test_users_resume_their_own_interrupted_job(app, credential):
    own, own_queue = app.seed_scan(USER, "user-orphan", status="orphaned")

    resumed = app.send(
        "POST", f"/api/scans/queue/{own_queue}/resume", _headers(app, USER, credential, unsafe=True)
    )

    assert resumed.status_code == 200, resumed.text
    assert app.scan(own).status == "queued"
    assert app.calls == ["wake"]


@pytest.mark.parametrize("credential", CREDENTIALS)
def test_pause_parks_own_job_and_uses_resume_authority(app, credential):
    own, own_queue = app.seed_scan(USER, "user-job")
    other, other_queue = app.seed_scan(PEER_USER, "peer-job")
    headers = _headers(app, USER, credential, unsafe=True)
    path = f"/api/scans/queue/{own_queue}"

    assert app.send("POST", f"/api/scans/queue/{other_queue}/pause", headers).status_code == 404
    assert app.scan(other).status == "queued"
    for _ in range(2):
        response = app.send("POST", f"{path}/pause", headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"paused": True, "pausing": False}
    assert app.scan(own).status == "paused"
    assert app.calls == []

    app.accounts.set_override(USER, C.SCAN_JOBS_RESUME_OWN, OverrideEffect.DENY)
    assert app.send("POST", f"{path}/resume", headers).status_code == 403
    app.accounts.set_override(USER, C.SCAN_JOBS_RESUME_OWN, OverrideEffect.ALLOW)
    assert app.send("POST", f"{path}/resume", headers).json() == {"resumed": True}
    assert app.scan(own).status == "queued"
    assert app.calls == ["wake"]


def test_a_denied_job_capability_refuses_even_the_callers_own_job(app):
    own, own_queue = app.seed_scan(USER, "user-job")
    app.accounts.set_override(USER, C.SCAN_JOBS_CANCEL_OWN, OverrideEffect.DENY)

    refused = app.send("DELETE", f"/api/scans/queue/{own_queue}", app.accounts.unsafe_cookie(USER))

    assert refused.status_code == 403
    assert refused.json()["code"] == "capability_denied"
    assert app.scan(own).status == "queued"


def test_only_admins_reorder_and_only_the_jobs_they_can_see(app):
    first, first_queue = app.seed_scan(USER, "in-a-1")
    hidden, hidden_queue = app.seed_scan(USER, "in-b", library=LIBRARY_B)
    last, last_queue = app.seed_scan(OWNER, "in-a-2")
    app.limit_to_library(PEER_ADMIN, LIBRARY_A)

    user = app.send(
        "POST",
        "/api/scans/queue/reorder",
        app.accounts.unsafe_cookie(USER),
        {"queue_ids": [last_queue, hidden_queue, first_queue]},
    )
    assert user.status_code == 403
    assert user.json()["code"] == "capability_denied"

    assert _queue(app, PEER_ADMIN) == ["in-a-1", "in-a-2"]
    partial = app.send(
        "POST",
        "/api/scans/queue/reorder",
        app.accounts.unsafe_cookie(PEER_ADMIN),
        {"queue_ids": [last_queue, first_queue]},
    )
    assert partial.status_code == 200, partial.text
    assert app.queued() == [last, hidden, first]

    naming_hidden = app.send(
        "POST",
        "/api/scans/queue/reorder",
        app.accounts.unsafe_cookie(PEER_ADMIN),
        {"queue_ids": [first_queue, hidden_queue, last_queue]},
    )
    assert naming_hidden.status_code == 409
    assert app.queued() == [last, hidden, first]


def _positions(app, user_id: int) -> list[tuple[str, int]]:
    response = app.send("GET", "/api/scans/queue", app.accounts.cookie(user_id))
    assert response.status_code == 200, response.text
    return [(item["name"], item["sort_order"]) for item in response.json()]


def test_queue_positions_count_only_the_jobs_the_caller_sees(app):
    app.seed_scan(PEER_USER, "peer-1")
    app.seed_scan(OWNER, "owner-1", library=LIBRARY_B)
    app.seed_scan(USER, "user-1")
    app.seed_scan(PEER_USER, "peer-2")
    app.limit_to_library(PEER_ADMIN, LIBRARY_A)

    assert _positions(app, USER) == [("user-1", 0)]
    assert _positions(app, ADMIN) == [("peer-1", 0), ("owner-1", 1), ("user-1", 2), ("peer-2", 3)]
    assert _positions(app, PEER_ADMIN) == [("peer-1", 0), ("user-1", 1), ("peer-2", 2)]

    item = app.jellyfin_item("1" * 32, LIBRARY_A)
    body = {"scan_audio": False, "scan_video": True, "jellyfin_item_id": item}
    created = app.send("POST", "/api/scans", app.accounts.unsafe_cookie(USER), body)
    assert created.status_code == 201, created.text
    assert created.json()["sort_order"] == 1
    assert _positions(app, USER) == [("user-1", 0), (created.json()["name"], 1)]


def test_a_partial_reorder_reports_positions_in_the_callers_frame(app):
    first, first_queue = app.seed_scan(USER, "in-a-1")
    hidden, _hidden_queue = app.seed_scan(USER, "in-b", library=LIBRARY_B)
    last, last_queue = app.seed_scan(OWNER, "in-a-2")
    app.limit_to_library(PEER_ADMIN, LIBRARY_A)

    reordered = app.send(
        "POST",
        "/api/scans/queue/reorder",
        app.accounts.unsafe_cookie(PEER_ADMIN),
        {"queue_ids": [last_queue, first_queue]},
    )

    assert reordered.status_code == 200, reordered.text
    assert app.queued() == [last, hidden, first]
    assert _positions(app, PEER_ADMIN) == [("in-a-2", 0), ("in-a-1", 1)]
    assert _positions(app, ADMIN) == [("in-a-2", 0), ("in-b", 1), ("in-a-1", 2)]
    assert _positions(app, USER) == [("in-b", 0), ("in-a-1", 1)]


def test_admin_job_operations_stay_within_their_media(app):
    hidden, hidden_queue = app.seed_scan(USER, "in-b", library=LIBRARY_B)
    app.limit_to_library(PEER_ADMIN, LIBRARY_A)

    assert "in-b" not in _queue(app, PEER_ADMIN)
    refused = app.send(
        "DELETE", f"/api/scans/queue/{hidden_queue}", app.accounts.unsafe_cookie(PEER_ADMIN)
    )
    assert refused.status_code == 404
    assert app.scan(hidden).status == "queued"

    allowed = app.send(
        "DELETE", f"/api/scans/queue/{hidden_queue}", app.accounts.unsafe_cookie(ADMIN)
    )
    assert allowed.status_code == 200, allowed.text
    assert app.scan(hidden).status == "cancelled"


def test_completed_scans_are_shared_evidence_for_everyone_with_media_access(app):
    shared, _ = app.seed_scan(OWNER, "owner-complete", status="complete")
    failed, _ = app.seed_scan(OWNER, "owner-failed", status="failed")
    outside, _ = app.seed_scan(OWNER, "owner-complete-b", library=LIBRARY_B, status="complete")
    app.limit_to_library(PEER_USER, LIBRARY_A)

    history = app.send("GET", "/api/scans/history", app.accounts.key(PEER_USER))
    assert history.status_code == 200
    assert [scan["name"] for scan in history.json()] == ["owner-complete"]
    assert (
        app.send("GET", f"/api/scans/{shared}/findings", app.accounts.key(PEER_USER)).status_code
        == 200
    )
    for hidden in (failed, outside):
        response = app.send("GET", f"/api/scans/{hidden}/findings", app.accounts.key(PEER_USER))
        assert response.status_code == 404

    renamed = app.send(
        "PATCH", f"/api/scans/{shared}", app.accounts.unsafe_cookie(PEER_USER), {"name": "mine now"}
    )
    assert renamed.status_code == 403
    assert renamed.json()["code"] == "capability_denied"
    assert app.scan(shared).name == "owner-complete"
    owner_history = app.send("GET", "/api/scans/history", app.accounts.key(OWNER)).json()
    assert {scan["name"] for scan in owner_history} == {
        "owner-complete",
        "owner-failed",
        "owner-complete-b",
    }


def test_deletion_counts_hidden_reviews_without_naming_them(app):
    scan_id, _ = app.seed_scan(USER, "user-complete", status="complete")
    app.seed_review(USER, "Mine", scan_id)
    app.seed_review(OWNER, "Owner private", scan_id)

    references = app.send("GET", f"/api/scans/{scan_id}/references", app.accounts.key(USER))
    assert references.status_code == 200, references.text
    assert [review["name"] for review in references.json()["reviews"]] == ["Mine"]
    assert references.json()["has_hidden_references"] is True
    assert "Owner private" not in references.text

    refused = app.send("DELETE", f"/api/scans/{scan_id}", app.accounts.unsafe_cookie(USER))
    assert refused.status_code == 409
    body = refused.text
    assert "scan_referenced" in body and "reviews you cannot view" in body
    assert "Owner private" not in body and "review-Owner private" not in body
    assert [review["name"] for review in refused.json()["detail"]["references"]["reviews"]] == [
        "Mine"
    ]
    assert app.scan(scan_id) is not None

    with app.accounts.factory() as db:
        from scanner.db import ReviewScanMembershipRow, SavedReviewRow

        db.query(ReviewScanMembershipRow).filter_by(review_id="review-Mine").delete()
        db.query(SavedReviewRow).filter_by(id="review-Mine").delete()
        db.commit()
    still = app.send("DELETE", f"/api/scans/{scan_id}", app.accounts.unsafe_cookie(USER))
    assert still.status_code == 409
    assert still.json()["detail"]["references"]["reviews"] == []
    assert "Owner private" not in still.text
    assert app.scan(scan_id) is not None

    owner = app.send("DELETE", f"/api/scans/{scan_id}", app.accounts.unsafe_cookie(OWNER))
    assert owner.status_code == 409
    assert "Owner private" in owner.text


def test_submission_is_decided_on_the_live_item_and_credited_to_the_caller(app):
    allowed = app.jellyfin_item("1" * 32, LIBRARY_A)
    denied = app.jellyfin_item("2" * 32, LIBRARY_B)
    app.limit_to_library(PEER_USER, LIBRARY_A)
    headers = app.accounts.unsafe_cookie(PEER_USER)
    body = {"scan_audio": False, "scan_video": True}

    refused = app.send("POST", "/api/scans", headers, {**body, "jellyfin_item_id": denied})
    assert refused.status_code == 404
    assert refused.json()["code"] == "not_found"
    assert app.queued() == []

    created = app.send("POST", "/api/scans", headers, {**body, "jellyfin_item_id": allowed})
    assert created.status_code == 201, created.text
    row = app.scan(created.json()["id"])
    assert (row.initiator_id, row.initiator_permission_revision) == (
        PEER_USER,
        app.revision(PEER_USER),
    )


def test_paths_and_others_model_progress_stay_hidden(app):
    app.seed_scan(OWNER, "owner-complete", status="complete", error=None)
    own, _ = app.seed_scan(USER, "user-acquiring", status="acquiring_model")
    failed, _ = app.seed_scan(USER, "user-failed", status="failed", error="")
    with app.accounts.factory() as db:
        from scanner.db import ScanRow

        db.get(
            ScanRow, failed
        ).error_message = f"Cannot open {app.scan(failed).resolved_local_path}"
        db.commit()

    path = app.scan(failed).resolved_local_path
    history = app.send("GET", "/api/scans/history", app.accounts.key(USER)).json()
    assert path not in str(history)
    assert {scan["name"] for scan in history} == {"owner-complete", "user-failed"}
    admin = app.send("GET", "/api/scans/history", app.accounts.key(ADMIN)).json()
    assert path in str(admin)

    queue = app.send("GET", "/api/scans/queue", app.accounts.key(USER)).json()
    assert [(item["name"], item["progress_percent"]) for item in queue] == [
        ("user-acquiring", 40.0)
    ]
    app.accounts.set_override(USER, C.MODELS_PROGRESS_VIEW_OWN, OverrideEffect.DENY)
    hidden = app.send("GET", "/api/scans/queue", app.accounts.key(USER)).json()
    assert [(item["name"], item["progress_percent"]) for item in hidden] == [
        ("user-acquiring", None)
    ]
    assert app.scan(own).progress_percent == 40.0


def test_an_archived_item_is_refused_before_jellyfin_or_its_connection_status(app, monkeypatch):
    monkeypatch.setattr(root, "record_jellyfin_connection", record_jellyfin_connection)
    jellyfin = root.JellyfinClient

    def missing(item_id):
        jellyfin.calls.append(f"jellyfin:{item_id}")
        raise JellyfinNotFound("Jellyfin did not return the requested media item")

    monkeypatch.setattr(jellyfin, "item", missing)
    with app.accounts.factory() as db, archived_reads(db):
        db.add(
            MediaItemRow(
                id="archived-item",
                server_id="radarr:http://radarr.test",
                jellyfin_id="12",
                item_type="Movie",
                name="Archived",
                archived_source=ArchivedSource.RADARR,
            )
        )
        db.commit()

    def connection():
        with app.accounts.factory() as db:
            row = db.get(JellyfinSettingsRow, 1)
            return row.last_checked_at, row.last_error

    before = connection()
    for item in ("12", "archived-item"):
        body = {"jellyfin_item_id": item, "scan_audio": False, "scan_video": True}
        response = app.send("POST", "/api/scans", app.accounts.unsafe_cookie(OWNER), body)
        assert response.status_code == 404, response.text
    assert connection() == before
    assert not [call for call in app.calls if call.startswith("jellyfin:")]


def test_without_jellyfin_an_archived_item_gets_the_same_answer_as_any_item(app):
    with app.accounts.factory() as db, archived_reads(db):
        db.add(
            MediaItemRow(
                id="archived-item",
                server_id="radarr:http://radarr.test",
                jellyfin_id="12",
                item_type="Movie",
                name="Archived",
                archived_source=ArchivedSource.RADARR,
            )
        )
        db.delete(db.get(JellyfinSettingsRow, 1))
        db.commit()
    answers = set()
    for item in ("12", "archived-item", "never-seen"):
        body = {"jellyfin_item_id": item, "scan_audio": False, "scan_video": True}
        response = app.send("POST", "/api/scans", app.accounts.unsafe_cookie(OWNER), body)
        answers.add((response.status_code, response.text))
    assert answers == {(409, '{"detail":"Configure Jellyfin before creating a scan"}')}
