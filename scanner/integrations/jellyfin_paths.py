"""Place a Jellyfin server path in the library whose reported location holds it."""

from __future__ import annotations

from collections.abc import Sequence

from scanner.models import JellyfinLibrary
from scanner.server_paths import parse_server_path

_DOT_SEGMENTS = {".", ".."}


def _server_path(path: str) -> tuple[str, ...] | None:
    """A server path as its root followed by its folder names.

    The path splits by its form's separator rules (see `parse_server_path`). A
    path with no established form, or with a `.` or `..` segment, has no
    establishable folder and yields None. Only the spelling is used; the path
    belongs to the Jellyfin server, not Editarr's filesystem.
    """

    parsed = parse_server_path(path)
    if parsed is None or _DOT_SEGMENTS.intersection((*parsed.root[1:], *parsed.names)):
        return None
    return (*parsed.root, *(name for name in parsed.names if name))


def library_of(path: str | None, found: Sequence[JellyfinLibrary]) -> str | None:
    """The one library whose reported location contains a server path.

    A path under several libraries' locations, or under none, has no single
    library, so no local scope can grant it. So does a path whose containment
    cannot be established (see `_server_path`).
    """

    target = _server_path(path) if path else None
    if target is None:
        return None
    owners = {
        library.id
        for library in found
        for location in library.locations
        if (prefix := _server_path(location)) and target[: len(prefix)] == prefix
    }
    return owners.pop() if len(owners) == 1 else None
