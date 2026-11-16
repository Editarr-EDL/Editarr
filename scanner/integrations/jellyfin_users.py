"""Read verified Jellyfin user identity and media access from the configured server.

Assumes the Jellyfin 12.0.0 REST routes and shapes recorded in
tests/fixtures/multi-user/jellyfin-users-v1. HTTP failures become `Unverified`
outcomes instead of exceptions, so no caller can read an outage as a deletion.
Every operation reads the server ID afresh and refuses a server other than the
first one this adapter saw. A password or Jellyfin access token is used for one
exchange and never kept.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TypeVar

import httpx

from scanner.security.access.identity import JellyfinServerId, ProviderIdentity

from .jellyfin import JellyfinError, media_browser_authorization
from .jellyfin_catalog import JellyfinCatalog, read_catalog
from .jellyfin_library_folders import LibraryFolders, parse_library_folders
from .jellyfin_server_url import normalize_server_url
from .jellyfin_user_snapshot import (
    CredentialProof,
    InvalidPayload,
    ItemAccess,
    ItemAccessSnapshot,
    JellyfinServer,
    JellyfinUserSnapshot,
    ServerMismatch,
    SessionCleanup,
    Unverified,
    UnverifiedReason,
    UserAvatar,
    UserDeleted,
    UserVerification,
    canonical_guid,
    parse_access_token,
    parse_authentication,
    parse_item_id,
    parse_item_ids,
    parse_server,
    parse_user,
    parse_users,
)
from .jellyfin_user_transport import Reply, ResponseLimits, exchange, open_client

logger = logging.getLogger(__name__)
T = TypeVar("T")

# Jellyfin replaces an earlier session for the same user and device on sign-in.
_CLIENT = {
    "Client": "Editarr",
    "Device": "Editarr",
    "DeviceId": "editarr-user-verification",
    "Version": "1",
}
_ID_BATCH = 100
_LOGOUT_ATTEMPTS = 3
_TRANSIENT = {
    UnverifiedReason.TIMEOUT,
    UnverifiedReason.UNREACHABLE,
    UnverifiedReason.SERVICE_ERROR,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


class JellyfinUserAdapter:
    """Verify Jellyfin users for one configured server URL and API key.

    The adapter owns its HTTP client. Tests replace only the `transport`, so no
    injected client setting can weaken the exchange guarantees.
    """

    def __init__(
        self,
        server_url: str,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] = _utc_now,
        limits: ResponseLimits = ResponseLimits(),
    ):
        if not api_key.strip():
            raise JellyfinError("Enter a Jellyfin API key")
        self.server_url = normalize_server_url(server_url)
        self._api_key = api_key.strip()
        self._client = open_client(transport, limits)
        self._clock = clock
        self._limits = limits
        self._pinned: JellyfinServerId | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JellyfinUserAdapter:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def server(self) -> JellyfinServer | Unverified:
        """Read the server identity now. The first ID read pins this adapter to that server."""
        reply = self._send("GET", "/System/Info/Public")
        if isinstance(reply, Unverified):
            return reply
        server = _parsed(lambda: parse_server(reply.json()))
        if isinstance(server, Unverified):
            return server
        self._pinned = self._pinned or server.server_id
        if server.server_id != self._pinned:
            return Unverified(UnverifiedReason.SERVER_MISMATCH)
        return server

    def authenticate(self, username: str, password: str) -> CredentialProof | Unverified:
        """Prove a user's Jellyfin credentials, then end the session Jellyfin opened.

        The name only selects the account to try. The result's identity comes from
        the server ID and the user GUID that Jellyfin returns. `Unverified` makes no
        claim about a session; only a `CredentialProof` reports the logout. Text
        that cannot be encoded is rejected without contacting Jellyfin.
        """
        try:
            username.encode("utf-8"), password.encode("utf-8")
        except UnicodeEncodeError:
            return Unverified(UnverifiedReason.CREDENTIALS_REJECTED)
        return self._prove("/Users/AuthenticateByName", {"Username": username, "Pw": password})

    def _prove(
        self, path: str, body: dict[str, str], *, unknown_rejected: bool = False
    ) -> CredentialProof | Unverified:
        server = self.server()
        if isinstance(server, Unverified):
            return server
        reply = self._send(
            "POST",
            path,
            json=body,
            authorization=media_browser_authorization(**_CLIENT),
            rejected=UnverifiedReason.CREDENTIALS_REJECTED,
            allow_not_found=unknown_rejected,
        )
        if isinstance(reply, Unverified):
            return reply
        if reply.status_code == 404:
            return Unverified(UnverifiedReason.CREDENTIALS_REJECTED)
        payload = _parsed(reply.json)
        if isinstance(payload, Unverified):
            return payload
        token = _parsed(lambda: parse_access_token(payload))
        if isinstance(token, Unverified):
            return token
        session = self._end_session(token)
        user = _parsed(lambda: parse_authentication(payload, server.server_id, self._clock()))
        if isinstance(user, Unverified):
            return user
        return CredentialProof(user, session)

    def verify_user(self, identity: ProviderIdentity) -> UserVerification:
        """Refresh one linked user. Deletion needs a 404 and a user list without the ID."""
        mismatch = self._require_server(identity.server_id)
        if mismatch is not None:
            return mismatch
        reply = self._send("GET", f"/Users/{identity.user_id.value}", allow_not_found=True)
        if isinstance(reply, Unverified):
            return reply
        if reply.status_code == 404:
            return self._confirm_deleted(identity)
        snapshot = _parsed(lambda: parse_user(reply.json(), identity.server_id, self._clock()))
        if isinstance(snapshot, JellyfinUserSnapshot) and snapshot.identity != identity:
            return Unverified(UnverifiedReason.INVALID_RESPONSE)
        return snapshot

    def users(self) -> tuple[JellyfinUserSnapshot, ...] | Unverified:
        """List every user for import. Absence from this list is not a deletion."""
        server = self.server()
        if isinstance(server, Unverified):
            return server
        return self._list_users(server.server_id)

    def item_access(
        self, identity: ProviderIdentity, item_ids: Sequence[str]
    ) -> ItemAccessSnapshot | Unverified:
        """Decide item access by opening each item as the user, one request per item.

        Jellyfin 12's batch `/Items?Ids=...&userId=...` applies rating and tag
        limits but not library access, so only `/Items/{id}?userId=...` can allow.
        A server-key batch query then separates restricted items from missing ones.
        The server ID is read again afterwards so no decision spans a replacement.
        """
        mismatch = self._require_server(identity.server_id)
        if mismatch is not None:
            return mismatch
        requested = {raw: _guid_or_none(raw) for raw in item_ids}
        candidates = sorted({guid for guid in requested.values() if guid})
        visible: set[str] = set()
        for guid in candidates:
            allowed = self._user_can_open(identity, guid)
            if isinstance(allowed, Unverified):
                return allowed
            if allowed:
                visible.add(guid)
        known = self._existing_ids([guid for guid in candidates if guid not in visible])
        if isinstance(known, Unverified):
            return known
        mismatch = self._require_server(identity.server_id)
        if mismatch is not None:
            return mismatch
        decisions = {
            raw: ItemAccess.ALLOWED
            if guid in visible
            else ItemAccess.RESTRICTED
            if guid in known
            else ItemAccess.NOT_FOUND
            for raw, guid in requested.items()
        }
        return ItemAccessSnapshot(identity, MappingProxyType(decisions), self._clock())

    def library_folders(self, server_id: JellyfinServerId) -> LibraryFolders | Unverified:
        """Read every library folder of `server_id`, checking the server before and after."""
        mismatch = self._require_server(server_id)
        if mismatch is not None:
            return mismatch
        reply = self._send("GET", "/Library/VirtualFolders")
        if isinstance(reply, Unverified):
            return reply
        folders = _parsed(lambda: parse_library_folders(reply.json(), server_id, self._clock()))
        if isinstance(folders, Unverified):
            return folders
        return self._require_server(server_id) or folders

    def catalog(self, server_id: JellyfinServerId) -> JellyfinCatalog | Unverified:
        """List every item of `server_id`, checking the server before and after.

        A listing that changed while it was read is invalid, so the caller retries.
        """
        mismatch = self._require_server(server_id)
        if mismatch is not None:
            return mismatch
        catalog = _parsed(lambda: read_catalog(self._catalog_page, server_id, self._clock))
        if isinstance(catalog, Unverified):
            return catalog
        return self._require_server(server_id) or catalog

    def _catalog_page(self, path: str, query: dict[str, str]) -> object:
        reply = self._send("GET", path, params=query)
        return reply if isinstance(reply, Unverified) else _parsed(reply.json)

    def avatar(self, identity: ProviderIdentity, tag: str | None) -> UserAvatar | None | Unverified:
        """Fetch a user's avatar through the server key; None when the user has none.

        An image carries no server ID, so the server is read again before either answer.
        """
        mismatch = self._require_server(identity.server_id)
        if mismatch is not None:
            return mismatch
        reply = self._send(
            "GET",
            "/UserImage",
            params={"userId": identity.user_id.value, **({"tag": tag} if tag else {})},
            accept="image/*",
            allow_not_found=True,
        )
        if isinstance(reply, Unverified):
            return reply
        mismatch = self._require_server(identity.server_id)
        if mismatch is not None:
            return mismatch
        if reply.status_code == 404:
            return None
        if not reply.content_type.startswith("image/"):
            return Unverified(UnverifiedReason.INVALID_RESPONSE)
        return UserAvatar(reply.body, reply.content_type)

    def _require_server(self, server_id: JellyfinServerId) -> Unverified | None:
        server = self.server()
        if isinstance(server, Unverified):
            return server
        if server.server_id != server_id:
            return Unverified(UnverifiedReason.SERVER_MISMATCH)
        return None

    def _confirm_deleted(self, identity: ProviderIdentity) -> UserDeleted | Unverified:
        listing = self._list_users(identity.server_id)
        if isinstance(listing, Unverified):
            return listing
        if any(user.identity == identity for user in listing):
            return Unverified(UnverifiedReason.INVALID_RESPONSE)
        mismatch = self._require_server(identity.server_id)
        if mismatch is not None:
            return mismatch
        return UserDeleted(identity, self._clock())

    def _list_users(
        self, server_id: JellyfinServerId
    ) -> tuple[JellyfinUserSnapshot, ...] | Unverified:
        reply = self._send("GET", "/Users")
        if isinstance(reply, Unverified):
            return reply
        return _parsed(lambda: parse_users(reply.json(), server_id, self._clock()))

    def _user_can_open(self, identity: ProviderIdentity, item_id: str) -> bool | Unverified:
        reply = self._send(
            "GET",
            f"/Items/{item_id}",
            params={"userId": identity.user_id.value},
            allow_not_found=True,
        )
        if isinstance(reply, Unverified):
            return reply
        if reply.status_code == 404:
            return False
        opened = _parsed(lambda: parse_item_id(reply.json()))
        if isinstance(opened, Unverified) or opened != item_id:
            return Unverified(UnverifiedReason.INVALID_RESPONSE)
        return True

    def _existing_ids(self, item_ids: list[str]) -> frozenset[str] | Unverified:
        """Return which of `item_ids` the server key can see."""
        found: set[str] = set()
        for start in range(0, len(item_ids), _ID_BATCH):
            batch = item_ids[start : start + _ID_BATCH]
            reply = self._send(
                "GET", "/Items", params={"Ids": ",".join(batch), "Limit": str(len(batch))}
            )
            if isinstance(reply, Unverified):
                return reply
            ids = _parsed(lambda: parse_item_ids(reply.json()))
            if isinstance(ids, Unverified):
                return ids
            found |= ids & set(batch)
        return frozenset(found)

    def _end_session(self, token: str) -> SessionCleanup:
        """Log out the sign-in session, retrying transient failures a bounded number of times."""
        for _attempt in range(_LOGOUT_ATTEMPTS):
            reply = self._send(
                "POST",
                "/Sessions/Logout",
                authorization=media_browser_authorization(**_CLIENT, Token=token),
            )
            if not isinstance(reply, Unverified):
                return SessionCleanup.ENDED
            if reply.reason not in _TRANSIENT:
                break
        logger.warning("Jellyfin did not end the verification session (%s)", reply.reason)
        return SessionCleanup.MAY_REMAIN

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: object = None,
        authorization: str | None = None,
        accept: str = "application/json",
        rejected: UnverifiedReason = UnverifiedReason.KEY_REJECTED,
        allow_not_found: bool = False,
    ) -> Reply | Unverified:
        """Send one bounded request and classify every failure without echoing credentials."""
        headers = {
            "Authorization": authorization or media_browser_authorization(Token=self._api_key),
            "Accept": accept,
        }
        request = self._client.build_request(
            method, f"{self.server_url}{path}", params=params, json=json, headers=headers
        )
        image = accept.startswith("image/")
        return exchange(
            self._client,
            request,
            max_bytes=self._limits.image_bytes if image else self._limits.json_bytes,
            seconds=self._limits.seconds,
            rejected=rejected,
            allow_not_found=allow_not_found,
        )


def _parsed(build: Callable[[], T]) -> T | Unverified:
    try:
        return build()
    except ServerMismatch:
        return Unverified(UnverifiedReason.SERVER_MISMATCH)
    except InvalidPayload:
        return Unverified(UnverifiedReason.INVALID_RESPONSE)


def _guid_or_none(value: str) -> str | None:
    try:
        return canonical_guid(value)
    except InvalidPayload:
        return None
