"""Transport guarantees of the Jellyfin user adapter at its HTTP boundary.

Scripted transports and a local HTTP server stand in for Jellyfin. These tests
are not live-server validation.
"""

from __future__ import annotations

import gzip
import json
import secrets
import time
import tracemalloc

import httpx
import pytest

from scanner.integrations.jellyfin_user_snapshot import (
    CredentialProof,
    SessionCleanup,
    Unverified,
    UnverifiedReason,
)
from scanner.integrations.jellyfin_user_transport import ResponseLimits
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from tests.jellyfin_user_boundary import (
    FIXTURE,
    identity,
    local_server,
    scripted_transport,
    sign_in_result,
    user_dto,
)

JSON = {"Content-Type": "application/json"}
GZIP_JSON = {**JSON, "Content-Encoding": "gzip"}
SERVER_INFO = json.dumps(FIXTURE["servers"]["primary"]).encode()


def peak_bytes(operation):
    """Run `operation` and return its result with the peak traced Python allocation."""
    tracemalloc.start()
    try:
        return operation(), tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_a_caller_cannot_supply_the_http_client():
    with httpx.Client() as client, pytest.raises(TypeError):
        JellyfinUserAdapter("http://jellyfin.test", "key", client)


def test_a_credential_challenge_is_neither_read_nor_answered():
    posts: list[httpx.Request] = []
    challenge_bytes = 0

    class Challenge(httpx.SyncByteStream):
        def __iter__(self):
            nonlocal challenge_bytes
            for _chunk in range(256):
                challenge_bytes += 65536
                yield b"x" * 65536

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Sessions/Logout":
            return httpx.Response(204)
        posts.append(request)
        if len(posts) > 1:
            return httpx.Response(200, json=sign_in_result(user_dto("Blake")))
        challenge = 'Digest realm="jellyfin", nonce="n", qop="auth"'
        return httpx.Response(401, headers={"WWW-Authenticate": challenge}, stream=Challenge())

    adapter = JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=scripted_transport(answer)
    )

    outcome = adapter.authenticate("Blake", secrets.token_urlsafe(18))

    assert outcome == Unverified(UnverifiedReason.CREDENTIALS_REJECTED)
    assert (len(posts), challenge_bytes) == (1, 0)


BUDGET = ResponseLimits(json_bytes=1024 * 1024)
BOMB = gzip.compress(b'"' + b"x" * 32 * BUDGET.json_bytes + b'"')


def test_a_compressed_body_is_refused_before_it_expands_past_the_budget():
    closed = False

    class Bomb(httpx.SyncByteStream):
        def __iter__(self):
            yield BOMB

        def close(self):
            nonlocal closed
            closed = True

    transport = scripted_transport(
        lambda _request: httpx.Response(200, headers=GZIP_JSON, stream=Bomb())
    )
    adapter = JellyfinUserAdapter("http://jellyfin.test", "key", transport=transport, limits=BUDGET)

    outcome, peak = peak_bytes(adapter.users)

    assert outcome == Unverified(UnverifiedReason.RESPONSE_TOO_LARGE)
    assert closed
    assert peak < 3 * BUDGET.json_bytes


def test_a_compressed_network_body_is_refused_before_it_expands_past_the_budget():
    def answer(path):
        if path == "/System/Info/Public":
            return 200, JSON, SERVER_INFO, 0
        return 200, GZIP_JSON, BOMB, 0

    with local_server(answer) as url, JellyfinUserAdapter(url, "key", limits=BUDGET) as adapter:
        outcome, peak = peak_bytes(adapter.users)

    assert outcome == Unverified(UnverifiedReason.RESPONSE_TOO_LARGE)
    assert peak < 3 * BUDGET.json_bytes


def test_slow_response_headers_end_at_the_exchange_deadline():
    users = json.dumps([user_dto("Blake")]).encode()

    def answer(path):
        if path == "/System/Info/Public":
            return 200, JSON, SERVER_INFO, 0
        return 200, JSON, users, 1.0

    limits = ResponseLimits(seconds=0.1)
    with local_server(answer) as url, JellyfinUserAdapter(url, "key", limits=limits) as adapter:
        started = time.monotonic()
        outcome = adapter.users()
        elapsed = time.monotonic() - started

    assert outcome == Unverified(UnverifiedReason.TIMEOUT)
    assert elapsed < 0.5


class Late(httpx.SyncByteStream):
    """A body that yields `chunks`, waiting `delay` seconds before each and before its end."""

    def __init__(self, chunks: list[bytes], delay: float):
        self.chunks = chunks
        self.delay = delay

    def __iter__(self):
        for chunk in self.chunks:
            time.sleep(self.delay)
            yield chunk
        time.sleep(self.delay)


def test_an_empty_logout_reply_after_the_deadline_is_not_an_ended_session():
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Sessions/Logout":
            return httpx.Response(204, stream=Late([], 0.06))
        return httpx.Response(200, json=sign_in_result(user_dto("Blake")))

    adapter = JellyfinUserAdapter(
        "http://jellyfin.test",
        "key",
        transport=scripted_transport(answer),
        limits=ResponseLimits(seconds=0.04),
    )

    proof = adapter.authenticate("Blake", secrets.token_urlsafe(18))

    assert isinstance(proof, CredentialProof)
    assert proof.session == SessionCleanup.MAY_REMAIN


def test_a_not_found_reply_after_the_deadline_is_not_a_confirmed_absence():
    def answer(request: httpx.Request) -> httpx.Response:
        time.sleep(0.06)
        return httpx.Response(404)

    adapter = JellyfinUserAdapter(
        "http://jellyfin.test",
        "key",
        transport=scripted_transport(answer),
        limits=ResponseLimits(seconds=0.04),
    )

    assert adapter.avatar(identity("Blake"), None) == Unverified(UnverifiedReason.TIMEOUT)


def test_compressed_input_without_decoded_output_still_meets_the_deadline():
    users = gzip.compress(json.dumps([user_dto("Blake")]).encode())
    body = Late([users, *[b"\x00" * 100] * 3], 0.03)
    transport = scripted_transport(
        lambda _request: httpx.Response(200, headers=GZIP_JSON, stream=body)
    )
    adapter = JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=transport, limits=ResponseLimits(seconds=0.04)
    )

    assert adapter.users() == Unverified(UnverifiedReason.TIMEOUT)


USERS_GZIP = gzip.compress(json.dumps([user_dto("Blake")]).encode())


@pytest.mark.parametrize(
    ("encoding", "body", "reason"),
    [
        ("gzip", USERS_GZIP + b"\x00" * 2 * BUDGET.json_bytes, UnverifiedReason.RESPONSE_TOO_LARGE),
        ("gzip", USERS_GZIP[:-8], UnverifiedReason.INVALID_RESPONSE),
        ("gzip", USERS_GZIP[:10] + b"\xff" * 64, UnverifiedReason.INVALID_RESPONSE),
        ("br", USERS_GZIP, UnverifiedReason.INVALID_RESPONSE),
    ],
    ids=["trailing-input-over-budget", "truncated", "corrupt", "unsupported-encoding"],
)
def test_a_body_that_cannot_be_decoded_within_bounds_is_unverified(encoding, body, reason):
    headers = {**JSON, "Content-Encoding": encoding}
    transport = scripted_transport(
        lambda _request: httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))
    )
    adapter = JellyfinUserAdapter("http://jellyfin.test", "key", transport=transport, limits=BUDGET)

    assert adapter.users() == Unverified(reason)


def test_an_empty_logout_reply_labelled_compressed_still_ends_the_session():
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Sessions/Logout":
            return httpx.Response(204, headers={"Content-Encoding": "gzip"})
        return httpx.Response(200, json=sign_in_result(user_dto("Blake")))

    adapter = JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=scripted_transport(answer)
    )

    proof = adapter.authenticate("Blake", secrets.token_urlsafe(18))

    assert isinstance(proof, CredentialProof)
    assert proof.session == SessionCleanup.ENDED


@pytest.mark.parametrize(
    "body",
    [
        b"[" * 100_000 + b"]" * 100_000,
        json.dumps([{**user_dto("Blake"), "Name": "\ud800"}]).encode(),
        json.dumps([{**user_dto("Blake"), "PrimaryImageTag": "\udfff"}]).encode(),
    ],
    ids=["deep-nesting", "unpaired-surrogate-name", "unpaired-surrogate-tag"],
)
def test_malformed_provider_json_is_an_invalid_response(body):
    transport = scripted_transport(lambda _request: httpx.Response(200, content=body))
    adapter = JellyfinUserAdapter("http://jellyfin.test", "key", transport=transport)

    assert adapter.users() == Unverified(UnverifiedReason.INVALID_RESPONSE)


def test_a_sign_in_token_that_is_not_valid_text_is_an_invalid_response():
    logouts: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Sessions/Logout":
            logouts.append(request)
            return httpx.Response(204)
        payload = {**sign_in_result(user_dto("Blake")), "AccessToken": "\ud800"}
        return httpx.Response(200, content=json.dumps(payload).encode())

    adapter = JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=scripted_transport(answer)
    )

    outcome = adapter.authenticate("Blake", secrets.token_urlsafe(18))

    assert outcome == Unverified(UnverifiedReason.INVALID_RESPONSE)
    assert logouts == []
