"""Jellyfin user adapter outages and secret handling against the jellyfin-users-v1 boundary.

Faults are served by a local HTTP server with synthetic Jellyfin 12.0.0 shapes.
These tests are not live-server validation.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scanner.integrations.jellyfin import JellyfinError
from scanner.integrations.jellyfin_user_snapshot import (
    CredentialProof,
    JellyfinUserSnapshot,
    SessionCleanup,
    Unverified,
    UnverifiedReason,
    UserDeleted,
)
from scanner.integrations.jellyfin_user_transport import ResponseLimits
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from tests.jellyfin_user_boundary import (
    FEATURE,
    FIXTURE,
    IDS,
    NOW,
    JellyfinBoundary,
    free_port,
    identity,
    scripted_transport,
    sign_in_result,
    user_dto,
)

FAULT_REASONS = {
    "timeout": UnverifiedReason.TIMEOUT,
    "refused": UnverifiedReason.UNREACHABLE,
    "starting": UnverifiedReason.SERVICE_ERROR,
    "server-error": UnverifiedReason.SERVICE_ERROR,
    "gateway-page": UnverifiedReason.INVALID_RESPONSE,
    "truncated-json": UnverifiedReason.INVALID_RESPONSE,
}
OPERATIONS = {
    "verify_user": lambda adapter, password: adapter.verify_user(identity("Emery")),
    "users": lambda adapter, password: adapter.users(),
    "item_access": lambda adapter, password: adapter.item_access(identity("Casey"), [FEATURE]),
    "avatar": lambda adapter, password: adapter.avatar(identity("Blake"), None),
    "authenticate": lambda adapter, password: adapter.authenticate("Casey", password),
}


@pytest.fixture
def passwords() -> dict[str, str]:
    return {user_id: secrets.token_urlsafe(18) for user_id in IDS.values()}


@pytest.fixture
def boundary(passwords):
    with JellyfinBoundary(secrets.token_urlsafe(24), passwords) as server:
        yield server


def adapter_for(url: str, api_key: str, clock=lambda: NOW) -> JellyfinUserAdapter:
    return JellyfinUserAdapter(url, api_key, clock=clock, limits=ResponseLimits(seconds=0.3))


def test_every_recorded_fault_has_an_expected_outcome():
    assert set(FAULT_REASONS) | {"proxy-not-found", "stale-user-route"} == set(FIXTURE["faults"])


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("fault", FAULT_REASONS)
def test_an_outage_is_unverified_with_no_timestamp(boundary, passwords, fault, operation):
    url = f"http://127.0.0.1:{free_port()}" if fault == "refused" else boundary.url
    boundary.fault = fault
    boundary.state = "changed"

    outcome = OPERATIONS[operation](adapter_for(url, boundary.api_key), passwords[IDS["Casey"]])

    assert outcome == Unverified(FAULT_REASONS[fault])


def test_an_outage_after_a_success_keeps_the_earlier_verification_time(boundary):
    times = iter([NOW, NOW + timedelta(minutes=5)])
    adapter = adapter_for(boundary.url, boundary.api_key, clock=lambda: next(times))

    verified = adapter.verify_user(identity("Emery"))
    boundary.state = "changed"
    boundary.fault = "starting"
    later = adapter.verify_user(identity("Emery"))

    assert isinstance(verified, JellyfinUserSnapshot) and verified.verified_at == NOW
    assert later == Unverified(UnverifiedReason.SERVICE_ERROR)
    assert [field.name for field in dataclasses.fields(Unverified)] == ["reason"]
    assert next(times) == NOW + timedelta(minutes=5)


def test_a_proxy_404_is_not_a_confirmed_deletion(boundary):
    boundary.state = "changed"
    boundary.fault = "proxy-not-found"
    adapter = adapter_for(boundary.url, boundary.api_key)

    assert adapter.verify_user(identity("Emery")) == Unverified(UnverifiedReason.SERVICE_ERROR)
    assert adapter.verify_user(identity("Blake")) == Unverified(UnverifiedReason.SERVICE_ERROR)


def test_a_404_for_a_listed_user_is_not_a_confirmed_deletion(boundary):
    boundary.fault = "stale-user-route"
    adapter = adapter_for(boundary.url, boundary.api_key)

    assert adapter.verify_user(identity("Emery")) == Unverified(UnverifiedReason.INVALID_RESPONSE)
    boundary.state = "changed"
    assert adapter.verify_user(identity("Emery")) == UserDeleted(identity("Emery"), NOW)


def test_a_rejected_api_key_is_not_a_deletion(boundary, passwords):
    boundary.state = "changed"
    adapter = adapter_for(boundary.url, secrets.token_urlsafe(24))

    for operation in ("verify_user", "users", "item_access", "avatar"):
        outcome = OPERATIONS[operation](adapter, "")
        assert outcome == Unverified(UnverifiedReason.KEY_REJECTED), operation
    boundary.state = "baseline"
    assert isinstance(OPERATIONS["authenticate"](adapter, passwords[IDS["Casey"]]), CredentialProof)


def test_secrets_never_reach_outcomes_errors_or_logs(boundary, passwords, caplog):
    caplog.set_level(logging.DEBUG)
    wrong_key = secrets.token_urlsafe(24)
    wrong_password = secrets.token_urlsafe(18)
    seen: list[str] = []
    for state in FIXTURE["states"]:
        # The timeout fault returns no content and only slows this sweep.
        for fault in (None, *(name for name in FAULT_REASONS if name != "timeout")):
            boundary.state, boundary.fault = state, fault
            url = f"http://127.0.0.1:{free_port()}" if fault == "refused" else boundary.url
            for key in (boundary.api_key, wrong_key):
                adapter = adapter_for(url, key)
                for operation in OPERATIONS.values():
                    for password in (passwords[IDS["Casey"]], wrong_password):
                        seen.append(repr(operation(adapter, password)))
    with pytest.raises(JellyfinError) as error:
        JellyfinUserAdapter(f"http://user:{wrong_password}@127.0.0.1", boundary.api_key)

    assert boundary.issued_tokens and boundary.open_tokens == set()
    secret_values = [
        boundary.api_key,
        wrong_key,
        wrong_password,
        *passwords.values(),
        *boundary.issued_tokens,
    ]
    evidence = "\n".join([*seen, str(error.value), caplog.text, *boundary.requests])
    assert not [value for value in secret_values if value in evidence]


def test_utc_clock_is_the_default():
    transport = scripted_transport(lambda _request: httpx.Response(200, json=user_dto("Blake")))
    adapter = JellyfinUserAdapter("http://jellyfin.test", "key", transport=transport)
    snapshot = adapter.verify_user(identity("Blake"))

    assert snapshot.verified_at.tzinfo is UTC
    assert abs(snapshot.verified_at - datetime.now(UTC)) < timedelta(minutes=1)


@pytest.mark.parametrize("status", [302, 307, 308])
@pytest.mark.parametrize("operation", ["authenticate", "verify_user"])
def test_a_redirect_is_unverified_and_never_followed(status, operation):
    forwarded: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "collector.invalid":
            forwarded.append(request)
            return httpx.Response(204)
        return httpx.Response(status, headers={"Location": "https://collector.invalid/capture"})

    transport = scripted_transport(answer)
    adapter = JellyfinUserAdapter(
        "https://jellyfin.test", secrets.token_urlsafe(24), transport=transport
    )

    outcome = OPERATIONS[operation](adapter, secrets.token_urlsafe(18))

    assert outcome == Unverified(UnverifiedReason.REDIRECTED)
    assert forwarded == []


def test_an_avatar_over_the_image_budget_is_unverified_without_reading_it_all():
    chunks_read = 0

    class Avatar(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal chunks_read
            for _chunk in range(256):
                chunks_read += 1
                yield b"x" * 65536

    transport = scripted_transport(
        lambda _request: httpx.Response(200, headers={"Content-Type": "image/png"}, stream=Avatar())
    )
    adapter = JellyfinUserAdapter("http://jellyfin.test", "key", transport=transport)

    assert adapter.avatar(identity("Blake"), None) == Unverified(
        UnverifiedReason.RESPONSE_TOO_LARGE
    )
    assert chunks_read <= ResponseLimits().image_bytes // 65536 + 1


@pytest.mark.parametrize("encoding", ["identity", "gzip"])
def test_a_json_body_over_its_decoded_budget_is_unverified(encoding):
    body = json.dumps([user_dto(name) for name in IDS] * 20).encode()
    sent = gzip.compress(body) if encoding == "gzip" else body
    headers = {"Content-Type": "application/json", "Content-Encoding": encoding}
    transport = scripted_transport(
        lambda _request: httpx.Response(200, headers=headers, content=sent)
    )
    budget = ResponseLimits(json_bytes=len(body) - 1)

    tight = JellyfinUserAdapter("http://jellyfin.test", "key", transport=transport, limits=budget)
    roomy = JellyfinUserAdapter("http://jellyfin.test", "key", transport=transport)

    assert encoding == "identity" or len(sent) < budget.json_bytes
    assert tight.users() == Unverified(UnverifiedReason.RESPONSE_TOO_LARGE)
    assert len(roomy.users()) == len(IDS) * 20


def test_a_response_that_keeps_trickling_hits_the_total_deadline():
    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for _chunk in range(200):
                time.sleep(0.02)
                yield b" "

    transport = scripted_transport(lambda _request: httpx.Response(200, stream=Trickle()))
    adapter = JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=transport, limits=ResponseLimits(seconds=0.3)
    )
    started = time.monotonic()

    assert adapter.users() == Unverified(UnverifiedReason.TIMEOUT)
    assert time.monotonic() - started < 1.5


@pytest.mark.parametrize(
    ("logout_statuses", "session"),
    [
        ([204], SessionCleanup.ENDED),
        ([503, 503, 204], SessionCleanup.ENDED),
        ([503, 503, 503], SessionCleanup.MAY_REMAIN),
        ([401], SessionCleanup.MAY_REMAIN),
    ],
    ids=["ended", "ended-on-retry", "retries-exhausted", "token-refused"],
)
def test_a_failed_logout_is_retried_then_reported_with_the_proof(logout_statuses, session, caplog):
    result = sign_in_result(user_dto("Blake"))
    logouts: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Sessions/Logout":
            logouts.append(request)
            return httpx.Response(logout_statuses[len(logouts) - 1])
        return httpx.Response(200, json=result)

    adapter = JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=scripted_transport(answer), clock=lambda: NOW
    )

    proof = adapter.authenticate("Blake", secrets.token_urlsafe(18))

    assert isinstance(proof, CredentialProof)
    assert (proof.user.identity, proof.session) == (identity("Blake"), session)
    assert len(logouts) == len(logout_statuses)
    assert result["AccessToken"] not in caplog.text + repr(proof)
