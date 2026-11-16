"""Read a path that belongs to a media server's filesystem, not Editarr's."""

from __future__ import annotations

import re
from dataclasses import dataclass

_DRIVE = re.compile(r"[A-Za-z]:[\\/]")
_EITHER_SEPARATOR = re.compile(r"[\\/]")


class UnresolvablePath(ValueError):
    """A server path this host cannot turn into a local file path.

    It has no recognized form, or its names cannot be spelled here. Callers
    treat it as one item without a local path, never as a failed request.
    """


class UnrepresentablePath(UnresolvablePath):
    """A server path whose remaining names this host would split into other folders."""


@dataclass(frozen=True)
class ServerPath:
    """A server path's root, `("/",)`, `("C:",)`, or `("//", server, share)`, and
    the names after it as spelled, including empty names from doubled or
    trailing separators."""

    root: tuple[str, ...]
    names: tuple[str, ...]

    @property
    def spelling(self) -> str:
        """The path with `/` between its root parts and names."""

        match self.root:
            case ("//", server, share):
                head = f"//{server}/{share}"
            case ("/",):
                head = ""
            case (drive,):
                head = drive
        return "/".join((head, *self.names))


def parse_server_path(path: str) -> ServerPath | None:
    """Split a server path by the separator rules of its form.

    A `/`-rooted path splits only on `/`, because `\\` is an ordinary name
    character on a POSIX server. A drive (`C:\\`, `C:/`) or UNC
    (`\\\\server\\share`, `//server/share`) path splits on both. A relative,
    drive-relative, `\\`-rooted, or mixed-prefix (`/\\`, `\\/`) path, or a UNC
    path without a server and share, has no established form and yields None.
    """

    if path[:2] in {"//", "\\\\"}:
        parts = _EITHER_SEPARATOR.split(path[2:])
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return None
        return ServerPath(("//", *parts[:2]), tuple(parts[2:]))
    if _DRIVE.match(path):
        return ServerPath((path[:2],), tuple(_EITHER_SEPARATOR.split(path[3:])))
    if path.startswith("/") and not path.startswith("/\\"):
        return ServerPath(("/",), tuple(path[1:].split("/")))
    return None
