"""Verified Jellyfin user identity and media-access snapshots.

Parsed from the Jellyfin 12.0.0 `PublicSystemInfo`, `UserDto`, `UserPolicy`, and
`BaseItemDto` shapes recorded in tests/fixtures/multi-user/jellyfin-users-v1.
A user's identity is the server ID plus the user GUID. The name is display data
and never identifies anyone. Jellyfin has no email or timezone, so a snapshot
carries neither and cannot overwrite those Editarr preferences.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from scanner.formats.export_layout import ExportLayoutError, parse_jellyfin_user_id
from scanner.security.access.identity import JellyfinServerId, ProviderIdentity


class InvalidPayload(ValueError):
    """A Jellyfin response that does not match the recorded shape."""


class ServerMismatch(InvalidPayload):
    """A record that names a server other than the one the adapter verified."""


class UnverifiedReason(StrEnum):
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    SERVICE_ERROR = "service_error"
    INVALID_RESPONSE = "invalid_response"
    KEY_REJECTED = "key_rejected"
    CREDENTIALS_REJECTED = "credentials_rejected"
    SERVER_MISMATCH = "server_mismatch"
    """The configured server reports another server ID, as after a replacement."""
    REDIRECTED = "redirected"
    """The server answered with a redirect, which is never followed with credentials."""
    RESPONSE_TOO_LARGE = "response_too_large"


@dataclass(frozen=True)
class Unverified:
    """No new fact about the user. It has no timestamp, so it cannot advance verification."""

    reason: UnverifiedReason


@dataclass(frozen=True)
class JellyfinServer:
    server_id: JellyfinServerId
    name: str | None
    version: str | None


@dataclass(frozen=True)
class MediaPolicy:
    """The user's Jellyfin library and parental limits as last reported by the server.

    Item decisions come from `ItemAccessSnapshot`, which Jellyfin evaluates with
    these limits applied. These fields let callers scope libraries and notice changes.
    """

    all_libraries: bool
    library_ids: frozenset[str]
    """Canonical library GUIDs. Empty when `all_libraries` is true."""
    max_parental_rating: int | None
    max_parental_sub_rating: int | None
    blocked_tags: tuple[str, ...]
    allowed_tags: tuple[str, ...]
    blocked_unrated: frozenset[str]

    def allows_library(self, library_id: str) -> bool:
        if self.all_libraries:
            return True
        try:
            return canonical_guid(library_id) in self.library_ids
        except InvalidPayload:
            return False


@dataclass(frozen=True)
class JellyfinUserSnapshot:
    """One user's verified state. Disabled is a confirmed upstream state, not an outage."""

    identity: ProviderIdentity
    name: str
    avatar_tag: str | None
    is_administrator: bool
    is_disabled: bool
    media: MediaPolicy
    verified_at: datetime


@dataclass(frozen=True)
class UserDeleted:
    """The linked server confirmed that this user ID no longer exists."""

    identity: ProviderIdentity
    confirmed_at: datetime


UserVerification = JellyfinUserSnapshot | UserDeleted | Unverified


class SessionCleanup(StrEnum):
    ENDED = "ended"
    MAY_REMAIN = "may_remain"
    """Jellyfin did not confirm the logout. The session stays usable until Jellyfin
    replaces it at Editarr's next sign-in or an administrator ends it."""


@dataclass(frozen=True)
class CredentialProof:
    """Jellyfin accepted the credentials of `user`; `session` reports the logout."""

    user: JellyfinUserSnapshot
    session: SessionCleanup


class ItemAccess(StrEnum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    """The server key sees the item but the user does not."""
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ItemAccessSnapshot:
    """Per-item decisions keyed by the caller's item ID strings."""

    identity: ProviderIdentity
    decisions: Mapping[str, ItemAccess]
    verified_at: datetime


@dataclass(frozen=True)
class UserAvatar:
    content: bytes
    content_type: str


def canonical_guid(value: object) -> str:
    """Return a Jellyfin GUID as 32 lowercase hex digits, accepting N or D form."""
    if not isinstance(value, str):
        raise InvalidPayload("Expected a GUID string")
    try:
        return parse_jellyfin_user_id(value).value
    except ExportLayoutError as error:
        raise InvalidPayload("Expected a GUID") from error


def parse_server(payload: object) -> JellyfinServer:
    """Parse `/System/Info/Public`; the server ID must be a GUID."""
    record = _object(payload)
    return JellyfinServer(
        server_id=JellyfinServerId(canonical_guid(record.get("Id"))),
        name=_optional_text(record.get("ServerName")),
        version=_optional_text(record.get("Version")),
    )


def parse_user(
    payload: object, server_id: JellyfinServerId, verified_at: datetime
) -> JellyfinUserSnapshot:
    """Parse a `UserDto` from `server_id`. A missing policy flag fails closed."""
    record = _object(payload)
    _require_server(record.get("ServerId"), server_id)
    policy = _object(record.get("Policy"))
    name = _text(record.get("Name"))
    if not name.strip():
        raise InvalidPayload("User name is missing")
    try:
        user_id = parse_jellyfin_user_id(_text(record.get("Id")))
    except ExportLayoutError as error:
        raise InvalidPayload("User ID is invalid") from error
    all_libraries = _flag(policy, "EnableAllFolders")
    return JellyfinUserSnapshot(
        identity=ProviderIdentity(server_id=server_id, user_id=user_id),
        name=name,
        avatar_tag=_optional_text(record.get("PrimaryImageTag")),
        is_administrator=_flag(policy, "IsAdministrator"),
        is_disabled=_flag(policy, "IsDisabled"),
        media=MediaPolicy(
            all_libraries=all_libraries,
            library_ids=frozenset()
            if all_libraries
            else frozenset(canonical_guid(value) for value in _list(policy, "EnabledFolders")),
            max_parental_rating=_optional_int(policy.get("MaxParentalRating")),
            max_parental_sub_rating=_optional_int(policy.get("MaxParentalSubRating")),
            blocked_tags=tuple(_text(value) for value in _list(policy, "BlockedTags")),
            allowed_tags=tuple(_text(value) for value in _list(policy, "AllowedTags")),
            blocked_unrated=frozenset(_text(value) for value in _list(policy, "BlockUnratedItems")),
        ),
        verified_at=verified_at,
    )


def parse_access_token(payload: object) -> str:
    """Return the session token of an `AuthenticationResult`, which must be nonempty text."""
    token = _text(_object(payload).get("AccessToken"))
    if not token:
        raise InvalidPayload("Access token is missing")
    return token


def parse_authentication(
    payload: object, server_id: JellyfinServerId, verified_at: datetime
) -> JellyfinUserSnapshot:
    """Parse the user of an `AuthenticationResult` that `server_id` issued."""
    record = _object(payload)
    parse_access_token(record)
    _require_server(record.get("ServerId"), server_id)
    return parse_user(record.get("User"), server_id, verified_at)


def parse_users(
    payload: object, server_id: JellyfinServerId, verified_at: datetime
) -> tuple[JellyfinUserSnapshot, ...]:
    """Parse `/Users`. One malformed user rejects the whole list."""
    if not isinstance(payload, list):
        raise InvalidPayload("Expected a user list")
    return tuple(parse_user(record, server_id, verified_at) for record in payload)


def parse_item_ids(payload: object) -> frozenset[str]:
    """Parse the canonical item IDs of an `/Items` query result."""
    items = _object(payload).get("Items")
    if not isinstance(items, list):
        raise InvalidPayload("Expected an item list")
    return frozenset(parse_item_id(item) for item in items)


def parse_item_id(payload: object) -> str:
    """Parse the canonical ID of one `BaseItemDto`."""
    return canonical_guid(_object(payload).get("Id"))


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InvalidPayload("Expected an object")
    return value


def _require_server(value: object, server_id: JellyfinServerId) -> None:
    if canonical_guid(value) != server_id:
        raise ServerMismatch("Record names another server")


def _flag(record: dict[str, object], key: str) -> bool:
    value = record.get(key)
    if not isinstance(value, bool):
        raise InvalidPayload(f"{key} is missing")
    return value


def _list(record: dict[str, object], key: str) -> list[object]:
    value = record.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidPayload(f"{key} is not a list")
    return value


def _text(value: object) -> str:
    """Accept a string only if it is valid Unicode; JSON escapes can carry lone surrogates."""
    if not isinstance(value, str):
        raise InvalidPayload("Expected text")
    try:
        value.encode()
    except UnicodeEncodeError as error:
        raise InvalidPayload("Text is not valid Unicode") from error
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidPayload("Expected an integer")
    return value
