"""Sign a Jellyfin administrator in for first-run setup and mint Editarr's API key.

Setup has no stored key yet, so this client speaks for one administrator
session: it proves the credentials, keeps the session token only for this
setup, asks Jellyfin for a new `Editarr` key, and ends the session. Every
failure becomes a `SetupRefusal` instead of an exception. Jellyfin 12 accepts
only the `Authorization: MediaBrowser` header for these calls.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

import httpx

from .jellyfin import JellyfinError, media_browser_authorization
from .jellyfin_server_url import normalize_server_url
from .jellyfin_user_snapshot import (
    InvalidPayload,
    JellyfinUserSnapshot,
    ServerMismatch,
    Unverified,
    UnverifiedReason,
    parse_access_token,
    parse_authentication,
    parse_server,
)
from .jellyfin_user_transport import Reply, ResponseLimits, exchange, open_client

APP_NAME = "Editarr"
_CLIENT = {"Client": APP_NAME, "Device": APP_NAME, "Version": "1"}
T = TypeVar("T")
logger = logging.getLogger(__name__)
LEFT_BEHIND = (
    "Jellyfin may still hold an API key named Editarr from a setup that stopped; "
    "delete it in Jellyfin under Dashboard > API Keys"
)
_OFFLINE = {UnverifiedReason.UNREACHABLE, UnverifiedReason.TIMEOUT}


class SetupRefusal(StrEnum):
    """Why setup stored nothing. Each value is the API error code."""

    BAD_URL = "jellyfin_bad_url"
    UNREACHABLE = "jellyfin_unreachable"
    NOT_JELLYFIN = "jellyfin_not_jellyfin"
    INVALID_CREDENTIALS = "invalid_credentials"
    NOT_ADMINISTRATOR = "jellyfin_not_administrator"
    KEY_REFUSED = "jellyfin_key_refused"
    KEY_LEFT_BEHIND = "jellyfin_key_left_behind"
    ALREADY_COMPLETE = "setup_already_complete"


class SetupRefused(Exception):
    def __init__(self, refusal: SetupRefusal) -> None:
        super().__init__(refusal.value)
        self.refusal = refusal


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AdministratorSignIn:
    """A proven Jellyfin administrator and the session token setup may use once."""

    server_url: str
    user: JellyfinUserSnapshot
    token: str = field(repr=False)


class JellyfinSetupClient:
    """One setup exchange with one Jellyfin server. Tests replace only `transport`."""

    def __init__(
        self,
        server_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utc_now,
        limits: ResponseLimits = ResponseLimits(seconds=15.0),
    ) -> None:
        try:
            self.server_url = normalize_server_url(server_url)
        except JellyfinError as error:
            raise SetupRefused(SetupRefusal.BAD_URL) from error
        self._client = open_client(transport, limits)
        # Jellyfin keeps one session per device ID, so concurrent setups need their own.
        self._device = {**_CLIENT, "DeviceId": f"editarr-setup-{secrets.token_hex(8)}"}
        self._clock = clock
        self._limits = limits

    def __enter__(self) -> JellyfinSetupClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self._client.close()

    def sign_in_administrator(self, username: str, password: str) -> AdministratorSignIn:
        """Prove the credentials and require `Policy.IsAdministrator`.

        A non-administrator's session is ended before the refusal is raised.
        """
        server = _payload(self._send("GET", "/System/Info/Public"), SetupRefusal.NOT_JELLYFIN)
        server_id = _parse(lambda: parse_server(server)).server_id
        reply = self._send(
            "POST",
            "/Users/AuthenticateByName",
            json={"Username": username, "Pw": password},
            authorization=media_browser_authorization(**self._device),
            rejected=UnverifiedReason.CREDENTIALS_REJECTED,
        )
        payload = _payload(reply, SetupRefusal.NOT_JELLYFIN)
        token = _parse(lambda: parse_access_token(payload))
        try:
            user = _parse(lambda: parse_authentication(payload, server_id, self._clock()))
        except SetupRefused:
            self._log_out(token)
            raise
        signed_in = AdministratorSignIn(self.server_url, user, token)
        if not user.is_administrator:
            self.end_session(signed_in)
            raise SetupRefused(SetupRefusal.NOT_ADMINISTRATOR)
        return signed_in

    def mint_key(self, administrator: AdministratorSignIn) -> str:
        """Create a new `Editarr` key and return it.

        Jellyfin answers the create call with no body, so the new key is the
        one `Editarr` key absent from the listing taken just before it. A
        create call can succeed behind a failed reply, so any failure after it
        revokes every new `Editarr` key, and an unreadable listing raises
        `KEY_LEFT_BEHIND`.
        """
        before = self._editarr_keys(administrator)
        created = self._send(
            "POST",
            "/Auth/Keys",
            params={"app": APP_NAME},
            authorization=self._as(administrator.token),
            rejected=UnverifiedReason.KEY_REJECTED,
        )
        try:
            new = self._editarr_keys(administrator) - before
        except SetupRefused:
            logger.warning("%s; setup could not list Jellyfin's keys to remove it", LEFT_BEHIND)
            raise SetupRefused(SetupRefusal.KEY_LEFT_BEHIND) from None
        if not isinstance(created, Unverified) and len(new) == 1:
            return next(iter(new))
        self.revoke_keys(administrator, new)
        raise SetupRefused(SetupRefusal.KEY_REFUSED)

    def revoke_keys(self, administrator: AdministratorSignIn, keys: Iterable[str]) -> None:
        """Delete keys this setup minted; raise `KEY_LEFT_BEHIND` if Jellyfin kept any."""
        keys = tuple(keys)
        authorization = self._as(administrator.token)
        kept = sum(
            isinstance(
                self._send("DELETE", f"/Auth/Keys/{key}", authorization=authorization), Unverified
            )
            for key in keys
        )
        if kept:
            logger.warning("%s; Jellyfin refused to delete %d of %d", LEFT_BEHIND, kept, len(keys))
            raise SetupRefused(SetupRefusal.KEY_LEFT_BEHIND)
        if keys:
            logger.info("Setup stopped and revoked %d Jellyfin API key(s) it minted", len(keys))

    def end_session(self, administrator: AdministratorSignIn) -> None:
        """Log out the setup session. Its device ID is never reused, so nothing else ends it."""
        self._log_out(administrator.token)

    def _log_out(self, token: str) -> None:
        self._send("POST", "/Sessions/Logout", authorization=self._as(token))

    def _editarr_keys(self, administrator: AdministratorSignIn) -> frozenset[str]:
        reply = self._send("GET", "/Auth/Keys", authorization=self._as(administrator.token))
        payload = _payload(reply, SetupRefusal.KEY_REFUSED)
        try:
            items = payload["Items"]  # type: ignore[index]
            return frozenset(
                item["AccessToken"]
                for item in items
                if item.get("AppName") == APP_NAME and isinstance(item.get("AccessToken"), str)
            )
        except (TypeError, KeyError, AttributeError) as error:
            raise SetupRefused(SetupRefusal.KEY_REFUSED) from error

    def _as(self, token: str) -> str:
        return media_browser_authorization(**self._device, Token=token)

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: object = None,
        authorization: str | None = None,
        rejected: UnverifiedReason = UnverifiedReason.KEY_REJECTED,
    ) -> Reply | Unverified:
        headers = {"Accept": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        try:
            request = self._client.build_request(
                method, f"{self.server_url}{path}", params=params, json=json, headers=headers
            )
        except (httpx.InvalidURL, UnicodeError) as error:
            raise SetupRefused(SetupRefusal.BAD_URL) from error
        return exchange(
            self._client,
            request,
            max_bytes=self._limits.json_bytes,
            seconds=self._limits.seconds,
            rejected=rejected,
            allow_not_found=False,
        )


def _payload(reply: Reply | Unverified, otherwise: SetupRefusal) -> object:
    """The reply's JSON, or the refusal its failure names."""
    if isinstance(reply, Unverified):
        if reply.reason in _OFFLINE:
            raise SetupRefused(SetupRefusal.UNREACHABLE)
        if reply.reason is UnverifiedReason.CREDENTIALS_REJECTED:
            raise SetupRefused(SetupRefusal.INVALID_CREDENTIALS)
        raise SetupRefused(otherwise)
    return _parse(reply.json)


def _parse(build: Callable[[], T]) -> T:
    try:
        return build()
    except (InvalidPayload, ServerMismatch) as error:
        raise SetupRefused(SetupRefusal.NOT_JELLYFIN) from error
