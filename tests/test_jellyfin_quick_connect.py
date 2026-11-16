"""JF-04: the Jellyfin Quick Connect client calls and the per-browser attempt registry.

The adapter talks to `scripted_transport`, which answers with the shapes the
pinned Jellyfin 12 returned in the JF-04 probe.
"""

from __future__ import annotations

import json

import httpx
import pytest

from scanner.integrations.jellyfin_quick_connect import (
    QuickConnectRequest,
    authenticate_quick_connect,
    initiate_quick_connect,
    quick_connect_enabled,
    quick_connect_request,
)
from scanner.integrations.jellyfin_user_snapshot import (
    CredentialProof,
    SessionCleanup,
    Unverified,
    UnverifiedReason,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.security.jellyfin_accounts.quick_connect import (
    QuickConnectAttempts,
    QuickConnectState,
)
from tests.jellyfin_user_boundary import scripted_transport, sign_in_result, user_dto

SECRET = "2F1957AD76B95D334FEE5CECE21D836FCD9BEA715927571FB57E10E01EB84351"


def _result(authenticated=False, secret=SECRET, code="894255"):
    return {
        "Authenticated": authenticated,
        "Secret": secret,
        "Code": code,
        "DeviceId": "editarr-user-verification",
        "DeviceName": "Editarr",
        "AppName": "Editarr",
        "AppVersion": "1",
        "DateAdded": "2026-11-03T20:18:43.8336179Z",
    }


def _adapter(answer, seen=None):
    def record(request):
        if seen is not None:
            seen.append(request)
        return answer(request)

    return JellyfinUserAdapter(
        "http://jellyfin.test", "server-key", transport=scripted_transport(record)
    )


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, True, True),
        (200, False, False),
        (200, "yes", Unverified(UnverifiedReason.INVALID_RESPONSE)),
        (503, None, Unverified(UnverifiedReason.SERVICE_ERROR)),
    ],
)
def test_enabled_reads_jellyfins_answer(status, body, expected):
    with _adapter(lambda _request: httpx.Response(status, json=body)) as adapter:
        assert quick_connect_enabled(adapter) == expected


def test_initiate_sends_the_device_header_and_returns_the_code():
    seen = []
    with _adapter(lambda _request: httpx.Response(200, json=_result()), seen) as adapter:
        started = initiate_quick_connect(adapter)

    assert started == QuickConnectRequest(SECRET, "894255", False)
    assert SECRET not in repr(started)
    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/QuickConnect/Initiate")
    authorization = request.headers["Authorization"]
    assert authorization.startswith("MediaBrowser ") and 'DeviceId="' in authorization
    assert "Token=" not in authorization


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (401, "Quick connect is disabled", UnverifiedReason.CREDENTIALS_REJECTED),
        (200, {**_result(), "Secret": "not-hex"}, UnverifiedReason.INVALID_RESPONSE),
        (200, {**_result(), "Code": None}, UnverifiedReason.INVALID_RESPONSE),
        (200, {**_result(), "Authenticated": "false"}, UnverifiedReason.INVALID_RESPONSE),
        (502, None, UnverifiedReason.SERVICE_ERROR),
    ],
)
def test_initiate_refusals(status, body, reason):
    with _adapter(lambda _request: httpx.Response(status, json=body)) as adapter:
        assert initiate_quick_connect(adapter) == Unverified(reason)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, _result(), QuickConnectRequest(SECRET, "894255", False)),
        (200, _result(authenticated=True), QuickConnectRequest(SECRET, "894255", True)),
        (404, "Unknown secret", None),
        (401, "Quick connect is disabled", Unverified(UnverifiedReason.CREDENTIALS_REJECTED)),
        (200, _result(secret="AB" * 32), Unverified(UnverifiedReason.INVALID_RESPONSE)),
    ],
    ids=["pending", "approved", "expired", "disabled", "other-secret"],
)
def test_connect_reports_pending_approved_and_expired(status, body, expected):
    seen = []
    with _adapter(lambda _request: httpx.Response(status, json=body), seen) as adapter:
        assert quick_connect_request(adapter, SECRET) == expected
    assert seen[0].url.params["secret"] == SECRET


def test_an_approved_secret_proves_the_user_and_ends_jellyfins_session():
    seen = []

    def answer(request):
        if request.url.path == "/Sessions/Logout":
            return httpx.Response(204)
        assert json.loads(request.content) == {"Secret": SECRET}
        return httpx.Response(200, json=sign_in_result(user_dto("Blake")))

    with _adapter(answer, seen) as adapter:
        proof = authenticate_quick_connect(adapter, SECRET)

    assert isinstance(proof, CredentialProof)
    assert (proof.user.name, proof.session) == ("Blake", SessionCleanup.ENDED)
    assert [request.url.path for request in seen] == [
        "/Users/AuthenticateWithQuickConnect",
        "/Sessions/Logout",
    ]


@pytest.mark.parametrize("status", [401, 404])
def test_an_unapproved_unknown_or_disabled_secret_is_rejected(status):
    with _adapter(lambda _request: httpx.Response(status, text="Error")) as adapter:
        assert authenticate_quick_connect(adapter, SECRET) == Unverified(
            UnverifiedReason.CREDENTIALS_REJECTED
        )


def test_password_sign_in_still_reads_a_404_as_an_outage():
    with _adapter(lambda _request: httpx.Response(404)) as adapter:
        assert adapter.authenticate("Blake", "pw") == Unverified(UnverifiedReason.SERVICE_ERROR)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _request(authenticated=False, secret=SECRET):
    return QuickConnectRequest(secret, "894255", authenticated)


def test_an_attempt_moves_pending_approved_consumed_and_spends_its_secret_once():
    attempts = QuickConnectAttempts(clock=Clock())
    handle = attempts.begin(_request())

    assert attempts.claim(handle) is None
    assert attempts.observe(handle, _request(True)).state is QuickConnectState.APPROVED
    assert attempts.claim(handle) == SECRET
    assert attempts.claim(handle) is None
    spent = attempts.current(handle)
    assert (spent.state, spent.secret) == (QuickConnectState.CONSUMED, None)
    assert attempts.observe(handle, _request(True)).state is QuickConnectState.CONSUMED


@pytest.mark.parametrize(
    ("end", "state"),
    [
        (lambda attempts, handle: attempts.observe(handle, None), QuickConnectState.EXPIRED),
        (lambda attempts, handle: attempts.cancel(handle), QuickConnectState.CANCELLED),
    ],
)
@pytest.mark.parametrize("approved", [False, True], ids=["pending", "approved"])
def test_ended_attempts_drop_the_secret_and_stay_ended(end, state, approved):
    attempts = QuickConnectAttempts(clock=Clock())
    handle = attempts.begin(_request())
    if approved:
        attempts.observe(handle, _request(True))
    assert end(attempts, handle).state is state

    assert attempts.observe(handle, _request(True)).state is state
    assert attempts.claim(handle) is None
    assert attempts.current(handle).secret is None


def test_a_handle_names_only_its_own_attempt_and_attempts_age_out():
    clock = Clock()
    attempts = QuickConnectAttempts(clock=clock, lifetime=60)
    first = attempts.begin(_request())
    second = attempts.begin(_request(secret="AB" * 32), replacing=first)

    assert attempts.current(first).state is QuickConnectState.CANCELLED
    assert attempts.current(second).state is QuickConnectState.PENDING
    assert attempts.current("unknown") is None
    clock.now += 61
    assert attempts.current(second) is None


def test_a_full_registry_drops_ended_attempts_but_never_an_open_one():
    attempts = QuickConnectAttempts(clock=Clock(), capacity=2)
    ended = attempts.begin(_request())
    attempts.cancel(ended)
    victim = attempts.begin(_request())
    approved = attempts.begin(_request())
    attempts.observe(approved, _request(True))

    assert attempts.current(ended) is None
    assert attempts.begin(_request()) is None
    assert attempts.current(victim).state is QuickConnectState.PENDING
    assert attempts.claim(approved) == SECRET
    assert attempts.begin(_request()) is not None


def test_starts_are_limited_per_network_and_across_all_clients():
    clock = Clock()
    attempts = QuickConnectAttempts(
        clock=clock, starts_per_window=2, window=600, starts_per_minute=5
    )

    def waits(*addresses):
        return [getattr(attempts.admit(address), "retry_after", 0) for address in addresses]

    assert waits("2001:db8::1", "2001:db8::2", "2001:db8::3") == [0, 0, 600]
    assert waits("192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4") == [0, 0, 0, 60]
    assert waits("2001:db8:0:1::1") == [60]
    clock.now += 60
    assert waits("192.0.2.9", "2001:db8::ffff") == [0, 540]


def test_only_the_first_refusal_of_a_lockout_is_marked_first():
    clock = Clock()
    attempts = QuickConnectAttempts(clock=clock, starts_per_window=1, window=60)

    assert attempts.admit("192.0.2.1") is None
    assert [attempts.admit("192.0.2.1").first for _ in range(3)] == [True, False, False]
    clock.now += 60
    assert attempts.admit("192.0.2.1") is None
    assert attempts.admit("192.0.2.1").first is True
