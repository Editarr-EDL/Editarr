"""Jellyfin 12 Quick Connect: its four calls and their request shapes.

`POST /QuickConnect/Initiate` and `GET /QuickConnect/Connect` both answer a
`QuickConnectResult`. The secret completes the sign-in and Jellyfin does not
spend it, so it must stay on the Editarr server; only the code is shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .jellyfin import media_browser_authorization
from .jellyfin_user_snapshot import CredentialProof, InvalidPayload, Unverified, UnverifiedReason
from .jellyfin_users import _CLIENT, JellyfinUserAdapter, _parsed

_SECRET = re.compile(r"[0-9A-Fa-f]{32,128}")
_CODE = re.compile(r"[0-9]{4,12}")


@dataclass(frozen=True)
class QuickConnectRequest:
    """A Quick Connect request: its secret, its code, and whether a user approved it."""

    secret: str
    code: str
    authenticated: bool

    def __repr__(self) -> str:
        return f"QuickConnectRequest(code={self.code!r}, authenticated={self.authenticated})"


def parse_quick_connect(payload: object) -> QuickConnectRequest:
    """Parse a `QuickConnectResult`; the secret must be hex and the code digits."""
    if not isinstance(payload, dict):
        raise InvalidPayload("Expected a Quick Connect result")
    secret, code, authenticated = (
        payload.get("Secret"),
        payload.get("Code"),
        payload.get("Authenticated"),
    )
    if not isinstance(secret, str) or not _SECRET.fullmatch(secret):
        raise InvalidPayload("Quick Connect secret is invalid")
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        raise InvalidPayload("Quick Connect code is invalid")
    if not isinstance(authenticated, bool):
        raise InvalidPayload("Quick Connect state is missing")
    return QuickConnectRequest(secret, code, authenticated)


def quick_connect_enabled(adapter: JellyfinUserAdapter) -> bool | Unverified:
    """Whether the server offers Quick Connect now; Jellyfin answers this anonymously."""
    reply = adapter._send("GET", "/QuickConnect/Enabled")
    if isinstance(reply, Unverified):
        return reply
    enabled = _parsed(reply.json)
    if isinstance(enabled, bool | Unverified):
        return enabled
    return Unverified(UnverifiedReason.INVALID_RESPONSE)


def initiate_quick_connect(adapter: JellyfinUserAdapter) -> QuickConnectRequest | Unverified:
    """Start a Quick Connect request; `CREDENTIALS_REJECTED` means Quick Connect is disabled."""
    reply = adapter._send(
        "POST",
        "/QuickConnect/Initiate",
        authorization=media_browser_authorization(**_CLIENT),
        rejected=UnverifiedReason.CREDENTIALS_REJECTED,
    )
    if isinstance(reply, Unverified):
        return reply
    return _parsed(lambda: parse_quick_connect(reply.json()))


def quick_connect_request(
    adapter: JellyfinUserAdapter, secret: str
) -> QuickConnectRequest | None | Unverified:
    """Read a pending request; None once Jellyfin no longer knows it, as after expiry.

    `CREDENTIALS_REJECTED` means Quick Connect is disabled.
    """
    reply = adapter._send(
        "GET",
        "/QuickConnect/Connect",
        params={"secret": secret},
        rejected=UnverifiedReason.CREDENTIALS_REJECTED,
        allow_not_found=True,
    )
    if isinstance(reply, Unverified):
        return reply
    if reply.status_code == 404:
        return None
    request = _parsed(lambda: parse_quick_connect(reply.json()))
    if isinstance(request, QuickConnectRequest) and request.secret.upper() != secret.upper():
        return Unverified(UnverifiedReason.INVALID_RESPONSE)
    return request


def authenticate_quick_connect(
    adapter: JellyfinUserAdapter, secret: str
) -> CredentialProof | Unverified:
    """Prove the user who approved `secret`, then end the session Jellyfin opened.

    Jellyfin answers an unknown or unapproved secret with 404, which is
    `CREDENTIALS_REJECTED` here like a disabled Quick Connect.
    """
    return adapter._prove(
        "/Users/AuthenticateWithQuickConnect", {"Secret": secret}, unknown_rejected=True
    )
