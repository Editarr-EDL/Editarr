# ruff: noqa: F811
"""JF-04 HTTP tests: Jellyfin Quick Connect sign-in through the real routers and middleware.

Jellyfin is `jellyfin_quick_connect_support`'s loopback stand-in. Start limits and
cached Jellyfin answers are in `test_jellyfin_quick_connect_limits`.
"""

from __future__ import annotations

import json

from scanner.security.auth_audit import AuthenticationAuditLogger
from tests.jellyfin_quick_connect_support import (
    HANDLE,
    QC,
    _complete,
    _sessions,
    _start,
    _status,
    _use_handle,
    app,
    boundary,
)
from tests.multi_user_access_support import ADMIN, ORIGIN, OWNER
from tests.test_jellyfin_accounts_http import _linked, _sign_in
from tests.users_api_http_support import fresh

__all__ = ["app", "boundary"]


def test_an_approved_code_signs_in_the_approving_user_like_password_sign_in(app, boundary):
    assert app.client.get(QC, headers=ORIGIN).json() == {"enabled": True}
    code = _start(app)
    assert len(code) == 6 and code.isdigit()
    assert _status(app).json() == {"state": "pending", "code": code}
    assert _complete(app).json()["code"] == "quick_connect_pending"

    boundary.approve(code, "Casey")
    assert _status(app).json() == {"state": "approved", "code": code}
    signed_in = _complete(app, remember_me=True)

    assert signed_in.status_code == 200, signed_in.text
    body = signed_in.json()
    casey = _linked(app, "Casey")
    assert (body["state"], body["display_name"]) == ("authenticated", "Casey")
    assert body["access"]["principal"]["user_id"] == casey
    assert "editarr_session" in signed_in.cookies
    assert signed_in.headers["Cache-Control"] == "no-store"
    profile = app.ok("GET", f"/api/users/{casey}", OWNER)
    assert (profile["source"], profile["role"]) == ("jellyfin", "user")
    assert all(token not in boundary.open_tokens for token in boundary.issued_tokens)
    assert ("auth.sign_in", "succeeded", casey, casey) in [row[:4] for row in app.audit()]


def test_the_secret_never_reaches_the_browser(app, boundary):
    started = app.client.post(f"{QC}/initiate", headers=ORIGIN)
    (secret,) = boundary.quick_connect
    cookie = started.headers["set-cookie"]

    assert secret not in started.text and secret.lower() not in started.text
    assert secret not in cookie and "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower()
    boundary.approve(started.json()["code"], "Blake")
    for response in (_status(app), _complete(app)):
        assert secret not in response.text


def test_a_spent_attempt_cannot_sign_in_again(app, boundary):
    code = _start(app)
    handle = app.client.cookies[HANDLE]
    boundary.approve(code, "Blake")
    _status(app)
    assert _complete(app).status_code == 200
    assert HANDLE not in app.client.cookies
    sessions = _sessions(app)

    _use_handle(app, handle)
    replayed = _complete(app)

    assert (replayed.status_code, replayed.json()["code"]) == (409, "quick_connect_consumed")
    assert _status(app).json()["state"] == "consumed"
    assert _sessions(app) == sessions


def test_another_browser_cannot_complete_or_read_the_attempt(app, boundary):
    sessions = _sessions(app)
    code = _start(app)
    boundary.approve(code, "Blake")
    _status(app)
    app.client.cookies.clear()

    assert _status(app).status_code == 404
    assert _complete(app).json()["code"] == "quick_connect_not_found"
    _use_handle(app, "guessed")
    assert _complete(app).status_code == 404
    assert _sessions(app) == sessions


def test_an_expired_code_reports_expired_and_a_new_code_replaces_it(app, boundary):
    first = _start(app)
    old_handle = app.client.cookies[HANDLE]
    boundary.expire(first)

    assert _status(app).json() == {"state": "expired", "code": first}
    assert _complete(app).json()["code"] == "quick_connect_expired"
    second = _start(app)
    assert _status(app).json() == {"state": "pending", "code": second}

    _use_handle(app, old_handle)
    assert _status(app).json()["state"] == "expired"


def test_a_new_code_cancels_the_previous_one(app, boundary):
    first = _start(app)
    old_handle = app.client.cookies[HANDLE]
    _start(app)
    boundary.approve(first, "Blake")

    _use_handle(app, old_handle)
    assert _status(app).json()["state"] == "cancelled"
    assert _complete(app).json()["code"] == "quick_connect_cancelled"


def test_cancel_ends_the_attempt_and_leaves_no_session(app, boundary):
    sessions = _sessions(app)
    code = _start(app)
    handle = app.client.cookies[HANDLE]
    cancelled = app.client.post(f"{QC}/cancel", headers=ORIGIN)
    boundary.approve(code, "Blake")

    assert cancelled.status_code == 204
    assert HANDLE not in app.client.cookies
    _use_handle(app, handle)
    assert _status(app).json()["state"] == "cancelled"
    assert _complete(app).json()["code"] == "quick_connect_cancelled"
    assert _sessions(app) == sessions


def test_disabled_quick_connect_is_hidden_and_refused(app, boundary):
    code = _start(app)
    boundary.quick_connect_enabled = False

    assert app.client.get(QC, headers=ORIGIN).json() == {"enabled": False}
    refused = app.client.post(f"{QC}/initiate", headers=ORIGIN)
    assert (refused.status_code, refused.json()["code"]) == (403, "quick_connect_disabled")
    assert _status(app).json() == {"state": "expired", "code": code}


def test_a_disabled_editarr_account_is_refused_with_the_password_message(app, boundary):
    code = _start(app)
    boundary.approve(code, "Drew")
    _status(app)
    assert _complete(app).status_code == 200
    drew = _linked(app, "Drew")
    app.ok("PUT", f"/api/users/{drew}/status", ADMIN, {"status": "disabled"})
    app.client.cookies.clear()

    code = _start(app)
    boundary.approve(code, "Drew")
    _status(app)
    refused = _complete(app)
    password = _sign_in(app, "Drew")

    assert refused.status_code == password.status_code == 403
    assert refused.json() == password.json()
    assert refused.json()["code"] == "account_disabled"


def test_a_new_user_is_admitted_at_first_quick_connect_sign_in(app, boundary):
    app.client.cookies.clear()
    code = _start(app)
    boundary.approve(code, "Emery")
    _status(app)

    assert _complete(app).status_code == 200
    assert _linked(app, "Emery") is not None


def _quick_sign_in(app, boundary, name):
    app.client.cookies.clear()
    code = _start(app)
    boundary.approve(code, name)
    _status(app)
    signed_in = _complete(app)
    assert signed_in.status_code == 200, signed_in.text
    return _linked(app, name)


def test_quick_connect_roles_start_from_jellyfin_and_then_stay_as_the_owner_sets_them(
    app, boundary
):
    def roles():
        return [app.ok("GET", f"/api/users/{user_id}", OWNER)["role"] for user_id in (avery, casey)]

    avery, casey = (_quick_sign_in(app, boundary, name) for name in ("Avery", "Casey"))
    assert roles() == ["admin", "user"]

    app.ok("PUT", f"/api/users/{avery}/role", OWNER, {"role": "user", **fresh(OWNER)})
    assert _quick_sign_in(app, boundary, "Avery") == avery
    boundary.state = "changed"
    assert _quick_sign_in(app, boundary, "Casey") == casey

    assert roles() == ["user", "user"]


def test_an_outage_keeps_the_attempt_pending_and_reports_it(app, boundary):
    code = _start(app)
    boundary.fault = "starting"

    outage = _status(app)
    assert (outage.status_code, outage.json()["code"]) == (503, "jellyfin_unavailable")
    assert app.client.get(QC, headers=ORIGIN).json() == {"enabled": False}
    boundary.fault = None
    assert _status(app).json() == {"state": "pending", "code": code}


def _sign_ins(app):
    return [
        (outcome, json.loads(details))
        for action, outcome, *_, details in app.audit()
        if action == "auth.sign_in"
    ]


def test_the_sign_in_audit_names_the_method(app, boundary):
    assert _sign_in(app, "Casey").status_code == 200
    assert _sign_in(app, "Casey", "wrong").status_code == 401
    app.client.cookies.clear()
    code = _start(app)
    boundary.approve(code, "Blake")
    _status(app)
    assert _complete(app).status_code == 200

    methods = [(outcome, details.get("method")) for outcome, details in _sign_ins(app)]
    assert methods == [
        ("succeeded", "password"),
        ("failed", "password"),
        ("succeeded", "quick_connect"),
    ]


def test_refused_starts_and_sign_ins_are_audited_like_password_throttling(app, tmp_path):
    app.client.app.state.authentication_audit = AuthenticationAuditLogger(tmp_path / "logs")
    for _ in range(5):
        _sign_in(app, "Casey", "wrong")
    assert _sign_in(app, "Casey", "wrong").status_code == 429
    for _ in range(10):
        _start(app)
    for _ in range(3):
        assert app.client.post(f"{QC}/initiate", headers=ORIGIN).status_code == 429

    lines = (tmp_path / "logs" / "auth-audit.jsonl").read_text().splitlines()
    logged = [json.loads(line) for line in lines]
    throttles = [record for record in logged if record["event"] == "auth.login_throttled"]
    assert [(record["event"], record["route"]) for record in throttles] == [
        ("auth.login_throttled", "/api/auth/jellyfin/login"),
        *[("auth.login_throttled", f"{QC}/initiate")] * 3,
    ]
    throttled = [details for outcome, details in _sign_ins(app) if details["reason"] == "throttled"]
    assert throttled == [
        {"reason": "throttled", "method": "password"},
        {"reason": "throttled", "method": "quick_connect"},
    ]


def test_every_jellyfin_sign_in_is_written_to_the_authentication_log(app, boundary, tmp_path):
    app.client.app.state.authentication_audit = AuthenticationAuditLogger(tmp_path / "logs")
    assert _sign_in(app, "Casey").status_code == 200
    casey = _linked(app, "Casey")
    app.client.cookies.clear()
    assert _sign_in(app, "Casey", "wrong").status_code == 401
    assert _sign_in(app, "Casey", "x" * 4097).status_code == 401
    code = _start(app)
    boundary.approve(code, "Drew")
    _status(app)
    assert _complete(app).status_code == 200
    drew = _linked(app, "Drew")
    app.ok("PUT", f"/api/users/{drew}/status", ADMIN, {"status": "disabled"})
    app.client.cookies.clear()
    code = _start(app)
    boundary.approve(code, "Drew")
    _status(app)
    assert _complete(app).status_code == 403
    assert _sign_in(app, "Drew").status_code == 403
    for _ in range(3):
        _sign_in(app, "Casey", "wrong")
    assert _sign_in(app, "Casey", "wrong").status_code == 429

    lines = (tmp_path / "logs" / "auth-audit.jsonl").read_text().splitlines()
    logged = [
        (
            record["event"],
            record["outcome"],
            record["route"],
            record["http_status"],
            record["actor"],
            record["admin_user_id"],
            record["reason_code"],
            record["session_id"] is not None,
        )
        for record in map(json.loads, lines[1:])
    ]
    login = "/api/auth/jellyfin/login"
    complete = f"{QC}/complete"
    failed = ("auth.login", "failure", login, 401, "anonymous", None, "invalid_credentials", False)
    assert logged == [
        ("auth.login", "success", login, 200, "user", casey, None, True),
        failed,
        failed,
        ("auth.login", "success", complete, 200, "user", drew, None, True),
        ("auth.login", "denied", complete, 403, "anonymous", None, "account_disabled", False),
        ("auth.login", "denied", login, 403, "anonymous", None, "account_disabled", False),
        *[failed] * 3,
        ("auth.login_throttled", "denied", login, 429, "anonymous", None, None, False),
    ]
    assert "wrong" not in "\n".join(lines) and "Casey" not in "\n".join(lines)
