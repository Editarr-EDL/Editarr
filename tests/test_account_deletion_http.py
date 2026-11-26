"""Account deletion through the real HTTP routes: protection, retention, permanent deletion.

Community transport is the only mocked boundary: every httpx send is recorded and
refused, so a deletion that contacted the service would show a recorded call.
"""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from scanner.db import (
    ApiKeyRow,
    AuditEventRow,
    AuthSessionRow,
    CommunityOperationRow,
    MediaItemExportRow,
    SavedReviewRow,
    ScanRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.http import account_deletion_api, users_admin_api, users_api
from scanner.persistence.rows.db_account_dispositions import AccountDispositionRow
from tests.multi_user_access_support import (
    ADMIN,
    JELLYFIN_DOWN,
    MEMBER_PASSWORD,
    OWNER,
    OWNER_PASSWORD,
    PEER_ADMIN,
    PEER_USER,
    USER,
)
from tests.test_personal_exports_http import app as _exports_app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app

exports_app = _exports_app


@pytest.fixture
def app(exports_app, monkeypatch):
    return deletion_routes(exports_app, monkeypatch)


def deletion_routes(exports_app, monkeypatch):
    """Serve the account deletion and Users routes with community transport refused."""
    exports_app.client.app.include_router(account_deletion_api.router)
    exports_app.client.app.include_router(users_api.router)
    exports_app.client.app.include_router(users_admin_api.router)
    exports_app.remote_calls = []

    def refuse(_client, request, *_args, **_kwargs):
        exports_app.remote_calls.append(str(request.url))
        raise httpx.ConnectError("community transport is disabled in this test")

    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    return exports_app


def fresh(user):
    return {"password": OWNER_PASSWORD if user == OWNER else MEMBER_PASSWORD}


def preview(app, user, target, mode, destination=None):
    query = f"mode={mode}" + (f"&destination={destination}" if destination else "")
    return app.send("GET", f"/api/users/{target}/deletion?{query}", app.accounts.cookie(user))


def delete(app, user, target, mode, confirmation, files=(), *, destination=None, proof=True):
    body = {
        "mode": mode,
        "confirmation": confirmation,
        "files": [{"export_id": key, "choice": value} for key, value in files],
    }
    if destination is not None:
        body["destination_id"] = destination
    if proof:
        body["reauthentication"] = fresh(user)
    return app.send("POST", f"/api/users/{target}/deletion", app.accounts.unsafe_cookie(user), body)


def confirmed(app, user, target, mode, files=(), *, destination=None):
    body = preview(app, user, target, mode, destination)
    assert body.status_code == 200, body.text
    response = delete(
        app, user, target, mode, body.json()["confirmation"], files, destination=destination
    )
    assert response.status_code == 200, response.text
    return body.json(), response.json()


def code(response):
    payload = response.json()
    return response.status_code, payload.get("code") or payload.get("detail", {}).get("code")


def account(app, user_id):
    with app.accounts.factory() as db:
        row = db.get(UserRow, user_id)
        linked = db.get(UserProviderIdentityRow, user_id) is not None
        return (row.status, row.disable_cause, linked, row.username)


def files_on_disk(app):
    with app.accounts.factory() as db:
        return {
            row.id: (
                row.review_id,
                row.policy_owner_id,
                row.original_author_id,
                Path(row.destination_path).read_bytes()
                if Path(row.destination_path).exists()
                else None,
            )
            for row in db.query(MediaItemExportRow)
        }


def deletion_audits(app):
    with app.accounts.factory() as db:
        return [
            (row.outcome, row.actor_id, row.target_user_id, json.loads(row.details_json))
            for row in db.query(AuditEventRow)
            .filter(AuditEventRow.action == "account.delete")
            .order_by(AuditEventRow.id)
        ]


def remote_publication(app, owner):
    with app.accounts.factory() as db:
        db.add(
            CommunityOperationRow(
                operation_id=f"published-{owner}",
                service_url="https://community.example",
                kind="publish",
                state="acknowledged",
                updated_at="2026-01-01T00:00:00Z",
                owner_id=owner,
                actor_id=owner,
            )
        )
        db.commit()


def bulk(app, user, targets, *, proof=True, headers=None):
    payload = {"targets": targets} | ({"reauthentication": fresh(user)} if proof else {})
    return app.send(
        "POST", "/api/users/bulk-deletion", headers or app.accounts.unsafe_cookie(user), payload
    )


def test_owner_and_protected_admins_are_refused_alone_and_in_bulk(app):
    owner_before, admin_before = account(app, OWNER), account(app, PEER_ADMIN)
    assert code(preview(app, ADMIN, OWNER, "retain"))[0] == 403
    assert code(preview(app, ADMIN, PEER_ADMIN, "retain"))[0] == 403
    assert code(preview(app, OWNER, OWNER, "retain")) == (409, "own_account")
    target = preview(app, ADMIN, PEER_USER, "retain").json()["confirmation"]
    response = bulk(
        app,
        ADMIN,
        [
            {"user_id": OWNER, "mode": "retain", "confirmation": target},
            {"user_id": PEER_ADMIN, "mode": "retain", "confirmation": target},
            {"user_id": PEER_USER, "mode": "retain", "confirmation": target},
            {"user_id": USER, "mode": "retain", "confirmation": target},
        ],
    )
    assert response.status_code == 200, response.text
    outcomes = [
        (item["user_id"], item["outcome"], item["code"]) for item in response.json()["results"]
    ]
    assert outcomes == [
        (OWNER, "refused", "protected_account"),
        (PEER_ADMIN, "refused", "protected_account"),
        (PEER_USER, "applied", None),
        (USER, "refused", "stale_account_deletion"),
    ]
    assert account(app, OWNER) == owner_before
    assert account(app, PEER_ADMIN) == admin_before
    assert account(app, USER)[0] == "active"
    assert account(app, PEER_USER) == ("disabled", "local", False, None)
    _body, result = confirmed(app, OWNER, PEER_ADMIN, "retain")
    assert result["mode"] == "retain" and account(app, PEER_ADMIN)[0] == "disabled"
    assert [(row[0], row[2]) for row in deletion_audits(app)] == [
        ("denied", OWNER),
        ("denied", PEER_ADMIN),
        ("succeeded", PEER_USER),
        ("denied", USER),
        ("succeeded", PEER_ADMIN),
    ]


def test_every_mode_needs_an_interactive_session_and_a_fresh_password(app):
    """Ruling wave13-mu38-transfer: API keys cannot delete accounts in any mode."""
    before = account(app, USER)
    for mode, destination in (("retain", None), ("delete", None), ("transfer", PEER_USER)):
        query = f"mode={mode}" + (f"&destination={destination}" if destination else "")
        path = f"/api/users/{USER}/deletion"
        by_key = app.send("GET", f"{path}?{query}", app.accounts.key(ADMIN))
        assert code(by_key) == (403, "interactive_session_required")
        body = preview(app, ADMIN, USER, mode, destination).json()
        request = {"mode": mode, "confirmation": body["confirmation"]} | (
            {"destination_id": destination} if destination else {}
        )
        keyed = app.send(
            "POST", path, app.accounts.key(ADMIN), request | {"reauthentication": fresh(ADMIN)}
        )
        assert code(keyed) == (403, "interactive_session_required")
        missing = delete(
            app, ADMIN, USER, mode, body["confirmation"], destination=destination, proof=False
        )
        assert code(missing) == (403, "reauthentication_required")
        wrong = app.send(
            "POST",
            path,
            app.accounts.unsafe_cookie(ADMIN),
            request | {"reauthentication": {"password": "not the admin password"}},
        )
        assert code(wrong) == (403, "reauthentication_failed")
        assert account(app, USER) == before
    target = {"user_id": USER, "mode": "retain", "confirmation": "0" * 64}
    by_key = bulk(app, ADMIN, [target], headers=app.accounts.key(ADMIN))
    assert code(by_key) == (403, "interactive_session_required")
    unproven = bulk(app, ADMIN, [target], proof=False).json()["results"]
    assert [item["code"] for item in unproven] == ["reauthentication_required"]
    assert [row[3]["reason"] for row in deletion_audits(app)] == [
        "reauthentication_required",
        "reauthentication_failed",
    ] * 3 + ["reauthentication_required"]
    assert account(app, USER) == before


def test_a_jellyfin_outage_stops_a_bulk_deletion_before_any_account(app):
    before = account(app, USER)
    target = {"user_id": USER, "mode": "retain"}
    target["confirmation"] = preview(app, ADMIN, USER, "retain").json()["confirmation"]
    payload = {"targets": [target], "reauthentication": {"password": JELLYFIN_DOWN}}
    outage = app.send(
        "POST", "/api/users/bulk-deletion", app.accounts.unsafe_cookie(ADMIN), payload
    )
    assert code(outage) == (503, "jellyfin_unavailable")
    assert account(app, USER) == before


def test_retention_keeps_files_and_publications_but_ends_sign_in_and_key(app):
    review = seeded_review(app, USER, "kept")
    ids = [publish(app, review, format).json()["id"] for format in ("edl", "mcf")]
    remote_publication(app, USER)
    with app.accounts.factory() as db:
        db.add(
            UserProviderIdentityRow(
                user_id=USER,
                server_id="server",
                provider_user_id="e" * 32,
                link_state="verified",
            )
        )
        db.commit()
    before = files_on_disk(app)
    user_cookie, user_key = app.accounts.cookie(USER), app.accounts.key(USER)
    assert app.send("GET", f"/api/users/{USER}", user_cookie).status_code == 200
    body = preview(app, ADMIN, USER, "retain").json()
    assert body["reviews"] == {"count": 1, "hidden": 0, "ids": [review.id]}
    assert body["personal_files"] == 2 and body["files"] == []
    assert body["credentials"] == {
        "sessions": 1,
        "api_key": True,
        "linked": True,
    }
    assert body["community"] == {
        "identity": False,
        "operations": 1,
        "pending": 0,
        "master_links": 0,
        "master_notifications": 0,
        "master_preferences": False,
        "master_sync_states": 0,
    }
    response = delete(app, ADMIN, USER, "retain", body["confirmation"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["reviews_retained"] == [review.id] and result["reviews_deleted"] == []
    assert result["personal_files_kept"] == 2
    assert result["sessions_revoked"] == 1 and result["api_key_revoked"] is True
    assert result["identity_released"] is True and result["profile_removed"] is False
    assert files_on_disk(app) == before and all(before[key][3] for key in ids)
    for headers in (user_cookie, user_key):
        assert app.send("GET", f"/api/users/{USER}", headers).status_code == 401
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id).owner_id == USER
        assert db.get(CommunityOperationRow, f"published-{USER}").state == "acknowledged"
        assert (
            db.scalar(select(func.count()).select_from(ApiKeyRow).where(ApiKeyRow.user_id == USER))
            == 0
        )
        assert db.get(UserProviderIdentityRow, USER) is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuthSessionRow)
                .where(AuthSessionRow.user_id == USER, AuthSessionRow.revoked_at.is_(None))
            )
            == 0
        )
        record = db.get(AccountDispositionRow, USER)
        assert (record.mode, record.actor_id, record.confirmation) == (
            "retain",
            ADMIN,
            body["confirmation"],
        )
        assert json.loads(record.impact_json) == body
        stored = json.loads(record.result_json)
        assert stored["result"] == {
            key: value for key, value in result.items() if key != "replayed"
        }
        assert stored["inventory"]["reviews"] == [review.id]
    assert account(app, USER) == ("disabled", "local", False, None)
    listed = app.send("GET", "/api/users", app.accounts.cookie(ADMIN)).json()
    assert USER not in {item["id"] for item in listed["items"]} and ADMIN in {
        item["id"] for item in listed["items"]
    }
    for method, path, payload in (
        ("GET", f"/api/users/{USER}", None),
        ("PUT", f"/api/users/{USER}/status", {"status": "active"}),
    ):
        headers = (
            app.accounts.cookie(ADMIN) if method == "GET" else app.accounts.unsafe_cookie(ADMIN)
        )
        assert app.send(method, path, headers, payload).status_code == 404
    assert app.remote_calls == []


def test_permanent_deletion_keeps_shared_evidence_and_applies_each_file_choice(app):
    review = seeded_review(app, USER, "doomed")
    edl, mcf = (publish(app, review, format).json()["id"] for format in ("edl", "mcf"))
    shared = seeded_review(app, USER, "shared source")
    unreferenced, _queue = app.seed_scan(USER, "lonely", status="complete")
    with app.accounts.factory() as db:
        reused = db.get(SavedReviewRow, shared.id)
        reused.owner_id = reused.original_author_id = PEER_USER
        shared_scan = reused.memberships[0].scan_id
        own_scan = db.get(SavedReviewRow, review.id).memberships[0].scan_id
        db.commit()
    remote_publication(app, USER)
    before = files_on_disk(app)
    body = preview(app, ADMIN, USER, "delete").json()
    assert body["blockers"] == [] and body["reviews"]["count"] == 1
    assert {item["publication"]["id"]: item["choices"] for item in body["files"]} == {
        edl: ["remove", "retain"],
        mcf: ["remove", "retain"],
    }
    assert body["scans"]["hidden"] == 0
    assert {scan["scan_id"]: scan["required"] for scan in body["scans"]["items"]} == {
        own_scan: False,
        shared_scan: True,
        unreferenced: False,
    }
    missing = delete(app, ADMIN, USER, "delete", body["confirmation"], [(edl, "retain")])
    assert code(missing) == (422, "account_deletion_choice_required")
    assert files_on_disk(app) == before
    result = delete(
        app, ADMIN, USER, "delete", body["confirmation"], [(edl, "retain"), (mcf, "remove")]
    ).json()
    assert result["reviews_deleted"] == [review.id]
    assert (result["files_retained"], result["files_removed"]) == ([edl], [mcf])
    assert result["scans_deleted"] == [unreferenced]
    assert sorted(result["scans_kept"]) == sorted([own_scan, shared_scan])
    assert result["profile_removed"] is True and result["personal_files_kept"] == 1
    after = files_on_disk(app)
    assert mcf not in after and before[mcf][3] is not None
    assert after[edl] == (None, USER, USER, before[edl][3])
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id) is None
        assert db.get(SavedReviewRow, shared.id).owner_id == PEER_USER
        assert db.get(ScanRow, shared_scan) is not None and db.get(ScanRow, own_scan) is not None
        assert db.get(ScanRow, unreferenced) is None
        assert db.get(CommunityOperationRow, f"published-{USER}") is not None
    assert app.remote_calls == []
    assert deletion_audits(app)[-1] == (
        "succeeded",
        ADMIN,
        USER,
        {
            "key_revoked": True,
            "files_moved": 0,
            "files_removed": 1,
            "files_retained": 1,
            "identity_released": False,
            "jellyfin_blocked": False,
            "mode": "delete",
            "policy_files": 0,
            "reviews": 1,
            "scans_deleted": 1,
            "scans_kept": 2,
            "sessions_revoked": 1,
        },
    )


def test_permanent_deletion_blocks_on_running_jobs_and_hidden_reviews(app):
    from datetime import UTC, datetime

    from scanner.security.access.capability_ids import Capability as C
    from scanner.security.access.overrides import OverrideEffect
    from scanner.security.scan_jobs.authority import JobRefusal, JobRefused, decide_job

    seeded_review(app, USER, "outside")
    running, _queue = app.seed_scan(USER, "running", status="scanning")
    app.accounts.set_override(ADMIN, C.REVIEWS_DELETE_OTHER, OverrideEffect.DENY)
    body = preview(app, ADMIN, USER, "delete").json()
    assert body["blockers"] == [
        {"code": "account_jobs_running", "count": 1},
        {"code": "reviews_outside_access", "count": 1},
    ]
    assert body["jobs"] == {"waiting": [], "running": [running], "hidden": 0}
    refused = delete(app, ADMIN, USER, "delete", body["confirmation"])
    assert code(refused) == (409, "account_deletion_blocked")
    assert account(app, USER)[0] == "active"
    retained = preview(app, ADMIN, USER, "retain").json()
    assert retained["blockers"] == [] and retained["jobs"]["running"] == [running]
    waiting, _queue = app.seed_scan(USER, "waiting", status="queued")
    retained = preview(app, ADMIN, USER, "retain").json()
    assert retained["jobs"]["waiting"] == [waiting]
    result = delete(app, ADMIN, USER, "retain", retained["confirmation"]).json()
    assert sorted(result["jobs_stopped"]) == sorted([running, waiting])
    with app.accounts.factory() as db:
        for scan in (running, waiting):
            verdict = decide_job(db, scan, now=datetime.now(UTC))
            assert verdict == JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)


def test_stale_previews_and_retries_never_apply_twice(app):
    review = seeded_review(app, USER, "first")
    body = preview(app, ADMIN, USER, "retain").json()
    seeded_review(app, USER, "second")
    stale = delete(app, ADMIN, USER, "retain", body["confirmation"])
    assert code(stale) == (409, "stale_account_deletion")
    assert account(app, USER)[0] == "active"
    body, first = confirmed(app, ADMIN, USER, "retain")
    again = delete(app, ADMIN, USER, "retain", body["confirmation"])
    assert again.status_code == 200
    assert again.json() == first | {"replayed": True}
    assert first["replayed"] is False and review.id in first["reviews_retained"]
    for response in (
        delete(app, ADMIN, USER, "retain", "0" * 64),
        delete(app, ADMIN, USER, "delete", body["confirmation"]),
        delete(app, PEER_ADMIN, USER, "retain", body["confirmation"]),
        delete(app, PEER_USER, USER, "retain", body["confirmation"]),
        preview(app, ADMIN, USER, "retain"),
    ):
        assert response.status_code == 404
    assert [row[0] for row in deletion_audits(app)] == ["denied", "succeeded"]
    with app.accounts.factory() as db:
        assert db.scalar(select(func.count()).select_from(AccountDispositionRow)) == 1
