"""Authority-bound, bounded HTTPS transport for the community signature profile."""

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from scanner.security.community_identity import InstallationIdentity
from scanner.security.community_signatures import (
    SignatureProfileError,
    _url_components,
    sign_request,
)


class CommunityFailure(RuntimeError):
    """Machine-readable failure with no remote payload, query or secret in its message."""

    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class ServiceEndpoint:
    url: str
    audience: str

    def __post_init__(self):
        try:
            if not isinstance(self.url, str) or not isinstance(self.audience, str):
                raise ValueError("Expected configured endpoint and audience")
            _, authority, path, query = _url_components(self.url)
            if path != "/" or query != "?" or self.url != f"https://{authority}":
                raise ValueError("Expected service origin")
            if authority != self.audience:
                raise ValueError("Wrong audience")
        except (ValueError, SignatureProfileError) as error:
            raise CommunityFailure("invalid_endpoint") from error

    def target(self, path: str) -> str:
        if not path.startswith("/v1/") or "?" in path or "#" in path or "\\" in path:
            raise CommunityFailure("invalid_endpoint")
        target = self.url + path
        try:
            _url_components(target)
        except SignatureProfileError as error:
            raise CommunityFailure("invalid_endpoint") from error
        return target

    def confirmation_url(self, value: object, state_id: str) -> str:
        expected = self.url + "/v1/discord/confirm?state=" + state_id
        if value != expected or urlsplit(expected).netloc != self.audience:
            raise CommunityFailure("invalid_response")
        return expected


_REMOTE_CODES = {
    "recovery_required",
    "membership_required",
    "membership_unavailable",
    "suspended",
    "invalid_signature",
    "clock_skew",
    "invalid_enrollment",
    "enrollment_conflict",
    "invalid_profile",
    "name_unavailable",
    "operation_conflict",
    "service_unavailable",
    "rate_limited",
    "unauthorized",
    "invalid_enrollment_proof",
    "membership_ineligible",
    "invalid_rename",
    "invalid_display_name",
    "display_name_taken",
    "nonce_conflict",
    "name_generation_unavailable",
    "invalid_request",
    "key_inactive",
    "contributor_suspended",
    "replay_detected",
    "invalid_key_operation",
    "invalid_rotation_proof",
    "key_conflict",
    "key_operation_conflict",
    "operation_not_found",
    "recovery_expired",
    "invalid_recovery",
    "recovery_conflict",
    "artifact_unavailable",
    "contribution_not_found",
    "contribution_unavailable",
    "historical_key_untrusted",
    "download_limit",
    "download_query_limit",
    "invalid_publication",
    "invalid_artifact",
    "invalid_artifact_signature",
    "invalid_lineage",
    "publication_limit",
    "body_too_large",
    "revision_conflict",
    "object_conflict",
    "publication_not_available",
    "publication_retry_required",
    "sharing_permission_required",
    "source_permission_unknown",
    "source_permission_incompatible",
    "source_attribution_mismatch",
    "lookup_limit",
    "invalid_cursor",
    "missing_identity",
    "invalid_lookup",
    "invalid_import_receipt",
    "import_query_limit",
    "invalid_vote",
    "vote_ineligible",
    "vote_query_limit",
    "revision_not_available",
}

_RECOVERY_CANDIDATE_ROUTES = {
    ("POST", "/v1/keys/recovery-status"),
    ("POST", "/v1/keys/recover"),
}


def _is_recovery_candidate_route(method: str, path: str) -> bool:
    if (method, path) in _RECOVERY_CANDIDATE_ROUTES:
        return True
    prefix = "/v1/key-operations/key_recovery/"
    operation_id = path[len(prefix) :] if path.startswith(prefix) else ""
    return method == "GET" and re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", operation_id) is not None


class CommunityTransport:
    def __init__(
        self,
        endpoint: ServiceEndpoint,
        *,
        adapter=None,
        clock=time.time,
        delay=asyncio.sleep,
        before_send=None,
    ):
        self.endpoint = endpoint
        self.adapter = adapter
        self.clock = clock
        self.delay = delay
        self.before_send = before_send

    async def request(
        self,
        method: str,
        path: str,
        *,
        identity: InstallationIdentity,
        body: bytes = b"",
        operation_id: str | None = None,
        candidate_proof: bool = False,
        response_bytes: int = 16_384,
        verify_response_digest: bool = False,
    ) -> dict:
        """Retry uncertain outcomes with the same intent and a fresh signature each time."""
        if type(response_bytes) is not int or not 0 < response_bytes <= 3_000_000:
            raise ValueError("An explicit bounded response size is required")
        try:
            async with asyncio.timeout(35):
                return await self._request(
                    method,
                    path,
                    identity=identity,
                    body=body,
                    operation_id=operation_id,
                    candidate_proof=candidate_proof,
                    response_bytes=response_bytes,
                    verify_response_digest=verify_response_digest,
                )
        except TimeoutError:
            raise CommunityFailure("network_unavailable", retryable=True) from None

    async def _request(
        self,
        method,
        path,
        *,
        identity,
        body,
        operation_id,
        candidate_proof,
        response_bytes,
        verify_response_digest,
    ):
        target = self.endpoint.target(path)
        if identity.service_url not in {None, self.endpoint.url}:
            raise CommunityFailure("wrong_service")
        recovery = identity.recovery
        if candidate_proof and not (
            _is_recovery_candidate_route(method, path)
            and recovery is not None
            and recovery["state"] == "pending"
            and recovery["newKeyId"] == identity.key_id
        ):
            raise CommunityFailure("recovery_required")
        if identity.last_known_key_state not in {"unenrolled", "active"} and not candidate_proof:
            raise CommunityFailure("recovery_required")
        async with httpx.AsyncClient(
            transport=self.adapter,
            timeout=httpx.Timeout(10, connect=5),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt in range(3):
                if self.before_send is not None:
                    self.before_send()
                created = int(self.clock())
                signed = sign_request(
                    private_key=identity.private_key,
                    key_id=identity.key_id,
                    method=method,
                    url=target,
                    body=body,
                    created=created,
                    expires=created + 60,
                    nonce=secrets.token_urlsafe(16),
                    operation_id=operation_id,
                    content_type="application/json" if method != "GET" else None,
                )
                headers = {name: value for name, value in signed.items() if isinstance(value, str)}
                headers["accept"] = "application/json"
                headers["accept-encoding"] = "identity"
                try:
                    async with client.stream(
                        method, target, content=body, headers=headers
                    ) as response:
                        if 300 <= response.status_code < 400:
                            raise CommunityFailure("unexpected_redirect")
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise CommunityFailure("invalid_response")
                        if verify_response_digest and "content-encoding" in response.headers:
                            raise CommunityFailure("invalid_response")
                        contents = bytearray()
                        async for chunk in response.aiter_bytes():
                            contents.extend(chunk)
                            if len(contents) > response_bytes:
                                raise CommunityFailure("invalid_response")
                        if 200 <= response.status_code < 300 and verify_response_digest:
                            digest = base64.b64encode(hashlib.sha256(contents).digest()).decode(
                                "ascii"
                            )
                            if response.headers.get("content-digest") != f"sha-256=:{digest}:":
                                raise CommunityFailure("invalid_response")
                            if response.headers.get("content-length") != str(len(contents)):
                                raise CommunityFailure("invalid_response")
                        try:
                            payload = json.loads(contents)
                        except (ValueError, UnicodeError):
                            payload = None
                        if 200 <= response.status_code < 300:
                            if (
                                not isinstance(payload, dict)
                                or response.headers.get("content-type", "").split(";")[0]
                                != "application/json"
                            ):
                                raise CommunityFailure("invalid_response")
                            return payload
                        code = (
                            payload.get("error", {}).get("code")
                            if (
                                isinstance(payload, dict) and isinstance(payload.get("error"), dict)
                            )
                            else None
                        )
                        code = (
                            code
                            if isinstance(code, str) and code in _REMOTE_CODES
                            else "remote_error"
                        )
                        retryable = response.status_code in {429, 502, 503, 504}
                        failure = CommunityFailure(code, retryable=retryable)
                        retry_after = response.headers.get("retry-after", "")
                        if retry_after:
                            try:
                                wait = float(retry_after)
                            except ValueError:
                                raise failure from None
                            if not 0 <= wait <= 2:
                                raise failure
                        else:
                            wait = 0.25 * (2**attempt)
                        if not retryable:
                            raise failure
                except httpx.TransportError:
                    failure = CommunityFailure("network_unavailable", retryable=True)
                    wait = 0.25 * (2**attempt)
                if attempt == 2:
                    raise failure
                await self.delay(wait)
        raise CommunityFailure("network_unavailable", retryable=True)
