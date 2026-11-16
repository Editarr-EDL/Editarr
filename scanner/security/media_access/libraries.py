"""Where a target lies against the linked Jellyfin server's library folders.

The decision of 2026-10-22: an owner exception applies only to a file outside
every folder a Jellyfin library covers, after path mapping, and a file inside
one must correspond to an item the user may see. Containment compares
directory identities, so a symlinked, differently cased, or bind-mounted
spelling of a library folder still contains its files. A path counts as inside
when either its mapped spelling or its real path lies beneath a library folder,
so a symlink in a library that points elsewhere stays inside. Folder and target
identities are read before and after each placement; a change between the two
reads, or anything that cannot be identified, proves nothing. A library folder
missing from this host (often a library Editarr does not mount), or one whose
name this host cannot spell, only stops a file from being proven outside;
files in the mounted folders are placed as usual.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from scanner.integrations.jellyfin_library_folders import LibraryFolders
from scanner.server_paths import UnresolvablePath

from .matching import ResolvePath, Unresolved

FileId = tuple[int, int]
"""`(st_dev, st_ino)`: one directory however its path is spelled."""


@dataclass(frozen=True)
class LibraryRoots:
    """One complete library-folder read and the path mappings that make its folders local."""

    folders: LibraryFolders
    resolve: ResolvePath


@dataclass(frozen=True)
class Outside:
    """Every path of the target lies outside every library folder."""


@dataclass(frozen=True)
class InLibraries:
    """A path of the target lies inside the folders of these libraries."""

    library_ids: frozenset[str]


@dataclass(frozen=True)
class OutsideMounted:
    """Outside every library folder on this host, but some library folder is missing here."""


Placement = Outside | OutsideMounted | InLibraries | Unresolved
Folders = tuple[dict[FileId, frozenset[str]], bool]
"""Each mounted library folder's identity and its libraries, and whether any folder is missing."""


def _identity(path: str) -> FileId | None:
    """What `path` names now; None when unreadable or the filesystem reports no file ID."""

    try:
        found = os.stat(path)
    except (OSError, ValueError):
        return None
    return (found.st_dev, found.st_ino) if found.st_ino else None


def _roots(roots: LibraryRoots) -> Folders | None:
    """Read every library folder; None if one exists here but cannot be identified."""

    found: dict[FileId, set[str]] = defaultdict(set)
    missing = False
    for library, folders in roots.folders.libraries.items():
        for folder in folders:
            try:
                local = roots.resolve(folder)
            except UnresolvablePath:
                missing = True
                continue
            try:
                stat = os.stat(local) if os.path.isabs(local) else None
            except (FileNotFoundError, NotADirectoryError):
                stat = None
            except (OSError, ValueError):
                return None
            if stat is None:
                missing = True
            elif not stat.st_ino:
                return None
            else:
                found[(stat.st_dev, stat.st_ino)].add(library)
    return {identity: frozenset(libraries) for identity, libraries in found.items()}, missing


def _chain(path: str) -> tuple[FileId, ...] | None:
    """The identities of every directory above the path as spelled and above its real path."""

    if not os.path.isabs(path):
        return None
    try:
        real = Path(os.path.realpath(path, strict=True))
    except (OSError, ValueError):
        return None
    above = (real, *real.parents, *Path(path).parents)
    found = [_identity(str(part)) for part in above]
    return None if None in found else tuple(part for part in found if part is not None)


def place(roots: LibraryRoots, paths: Iterable[str]) -> Placement:
    """Place a target's paths, proving the folders and paths held still while they were read."""

    paths = tuple(paths)
    before = _roots(roots)
    chains = [_chain(path) for path in paths]
    if not paths or before is None or None in chains:
        return Unresolved()
    if _roots(roots) != before or [_chain(path) for path in paths] != chains:
        return Unresolved()
    folders, missing = before
    inside = frozenset().union(
        *(folders[part] for chain in chains if chain for part in chain if part in folders)
    )
    if inside:
        return InLibraries(inside)
    return OutsideMounted() if missing else Outside()
