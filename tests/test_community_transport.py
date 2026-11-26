import asyncio

import httpx
import pytest

from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.security.community_identity import load_or_create_installation_identity
from tests.community_identity_support import identity_home


@pytest.mark.parametrize(
    "url,audience",
    [
        ("http://community.test", "community.test"),
        ("https://community.test", "other.test"),
        ("https://community.test/", "community.test"),
        ("https://user@community.test", "community.test"),
        ("https://community.test?secret=value", "community.test"),
    ],
)
def test_rejects_wrong_endpoint(url, audience):
    with pytest.raises(CommunityFailure, match="invalid_endpoint"):
        ServiceEndpoint(url, audience)


def test_redirect_never_forwards_signature(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://attacker.test/"})

    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(handle),
    )
    with pytest.raises(CommunityFailure, match="unexpected_redirect"):
        asyncio.run(
            transport.request(
                "GET",
                "/v1/identity",
                identity=load_or_create_installation_identity(identity_home(tmp_path)),
            )
        )
    assert len(requests) == 1
    assert requests[0].url.host == "community.test"


def test_lost_response_retries_exact_intent_with_fresh_nonce(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadError("secret must not appear in errors", request=request)
        return httpx.Response(200, json={"ok": True})

    async def delay(seconds):
        assert seconds <= 2

    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(handle),
        delay=delay,
    )
    result = asyncio.run(
        transport.request(
            "POST",
            "/v1/enrollments/complete",
            identity=load_or_create_installation_identity(identity_home(tmp_path)),
            body=b'{"version":1}',
            operation_id="same-operation",
        )
    )
    assert result == {"ok": True}
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert requests[0].headers["idempotency-key"] == requests[1].headers["idempotency-key"]
    assert requests[0].headers["signature-input"] != requests[1].headers["signature-input"]


def test_bounded_response_and_redacted_failure(tmp_path):
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 16_385)),
    )
    with pytest.raises(CommunityFailure, match="invalid_response"):
        asyncio.run(transport.request("GET", "/v1/identity", identity=identity))


def test_clock_skew_is_actionable_redacted_and_not_automatically_retried(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"code": "clock_skew", "detail": "private"}})

    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(handle),
    )
    with pytest.raises(CommunityFailure, match="clock_skew") as error:
        asyncio.run(
            transport.request(
                "GET",
                "/v1/identity",
                identity=load_or_create_installation_identity(identity_home(tmp_path)),
            )
        )
    assert error.value.code == "clock_skew"
    assert not error.value.retryable
    assert str(error.value) == "clock_skew"
    assert len(requests) == 1


def test_provider_backoff_is_bounded_and_errors_are_redacted(tmp_path):
    attempts = []
    delays = []

    def handle(request):
        attempts.append(request)
        return httpx.Response(
            429,
            json={"error": {"code": "rate_limited", "secret": "private"}},
            headers={"retry-after": "0.1"},
        )

    async def delay(seconds):
        delays.append(seconds)

    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(handle),
        delay=delay,
    )
    with pytest.raises(CommunityFailure, match="rate_limited") as error:
        asyncio.run(
            transport.request(
                "GET",
                "/v1/identity",
                identity=load_or_create_installation_identity(identity_home(tmp_path)),
            )
        )
    assert error.value.retryable
    assert "private" not in str(error.value)
    assert len(attempts) == 3 and delays == [0.1, 0.1]


@pytest.mark.parametrize(
    "code",
    [
        "recovery_expired",
        "invalid_recovery",
        "recovery_conflict",
        "key_operation_conflict",
        "invalid_vote",
        "vote_ineligible",
    ],
)
def test_known_recovery_and_key_operation_errors_survive_http_boundary(tmp_path, code):
    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(
            lambda _: httpx.Response(409, json={"error": {"code": code, "detail": "private"}})
        ),
    )
    with pytest.raises(CommunityFailure, match=code) as error:
        asyncio.run(
            transport.request(
                "GET",
                "/v1/identity",
                identity=load_or_create_installation_identity(identity_home(tmp_path)),
            )
        )
    assert error.value.code == code
    assert "private" not in str(error.value)


def test_unknown_remote_error_code_and_payload_are_redacted(tmp_path):
    transport = CommunityTransport(
        ServiceEndpoint("https://community.test", "community.test"),
        adapter=httpx.MockTransport(
            lambda _: httpx.Response(
                409,
                json={
                    "error": {
                        "code": "private_internal_error",
                        "message": "do not disclose this payload",
                        "secret": "private credential",
                    }
                },
            )
        ),
    )
    with pytest.raises(CommunityFailure, match="remote_error") as error:
        asyncio.run(
            transport.request(
                "GET",
                "/v1/identity",
                identity=load_or_create_installation_identity(identity_home(tmp_path)),
            )
        )
    assert error.value.code == "remote_error"
    assert "private_internal_error" not in str(error.value)
    assert "do not disclose this payload" not in str(error.value)
    assert "private credential" not in str(error.value)
