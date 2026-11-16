"""The folders a Jellyfin server's libraries cover, read whole or not at all.

Media access needs proof that a file lies outside every library folder before an
owner exception can apply. A partial or failed read proves nothing, so the only
way to obtain `LibraryFolders` is a complete `/Library/VirtualFolders` parse.
A `boxsets` library is Jellyfin's Collections library: its folder under the
server's data directory holds collection metadata, never media, so it is left out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from scanner.security.access.identity import JellyfinServerId

from .jellyfin_user_snapshot import InvalidPayload, canonical_guid

_SEAL = object()
_COLLECTIONS = "boxsets"


@dataclass(frozen=True)
class LibraryFolders:
    """Every folder of every media library of one server, as the server spells them.

    `libraries` maps each canonical library GUID to its folders. Only
    `parse_library_folders` builds one, so holding it proves one complete read.
    """

    server_id: JellyfinServerId
    libraries: Mapping[str, frozenset[str]]
    read_at: datetime
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise TypeError("LibraryFolders comes only from a complete library-folder read")


def parse_library_folders(
    payload: object, server_id: JellyfinServerId, read_at: datetime
) -> LibraryFolders:
    """Parse a `/Library/VirtualFolders` reply; any malformed library rejects the whole read."""

    if not isinstance(payload, list):
        raise InvalidPayload("Library folders must be a list")
    libraries: dict[str, set[str]] = {}
    for library in payload:
        if not isinstance(library, dict):
            raise InvalidPayload("A library must be an object")
        kind = library.get("CollectionType")
        if kind is not None and not isinstance(kind, str):
            raise InvalidPayload("A library type must be text")
        library_id = canonical_guid(library.get("ItemId"))
        locations = library.get("Locations")
        if not isinstance(locations, list):
            raise InvalidPayload("A library must list its folders")
        for location in locations:
            if not isinstance(location, str) or not location.strip():
                raise InvalidPayload("A library folder must be a path")
        if kind != _COLLECTIONS:
            libraries.setdefault(library_id, set()).update(locations)
    frozen = MappingProxyType({key: frozenset(value) for key, value in libraries.items()})
    return LibraryFolders(server_id, frozen, read_at, _SEAL)
