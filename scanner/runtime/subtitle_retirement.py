"""Move an old subtitle into recoverable storage before checking its ownership."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .durability import atomic_write_bytes, sync_directory

_PREFIX = ".editarr-subtitle-retirement-"


@dataclass(frozen=True)
class SubtitleQuarantine:
    directory: Path

    @property
    def proof(self) -> Path:
        return self.directory / "ownership.json"

    @property
    def held(self) -> Path:
        return self.directory / "subtitle.held"

    def discard(self) -> None:
        """Remove the held copy after its ownership is verified or it is restored."""
        self.held.unlink(missing_ok=True)
        sync_directory(self.directory)
        self.proof.unlink(missing_ok=True)
        self.directory.rmdir()
        sync_directory(self.directory.parent)

    def restore(self, original: Path) -> bool:
        """Restore a moved file only while the original path is free."""
        try:
            os.link(self.held, original, follow_symlinks=False)
        except FileExistsError:
            # A prior recovery may have linked it back before exiting.
            try:
                return self.held.lstat().st_ino == original.lstat().st_ino
            except OSError:
                return False
        except (NotImplementedError, OSError):
            return False
        sync_directory(original.parent)
        return True


def pending_subtitle_quarantines(parent: Path):
    """Find private staging directories left by an interrupted publication."""
    for candidate in parent.iterdir():
        if candidate.name.startswith(_PREFIX) and stat.S_ISDIR(candidate.lstat().st_mode):
            yield SubtitleQuarantine(candidate)


def quarantine_subtitle(path: Path, proof: bytes) -> SubtitleQuarantine:
    """Persist the ownership proof, then atomically move the actual directory entry."""
    directory = Path(tempfile.mkdtemp(dir=path.parent, prefix=_PREFIX))
    quarantine = SubtitleQuarantine(directory)
    try:
        atomic_write_bytes(quarantine.proof, proof, prefix=".ownership-", sync_dir=True)
        sync_directory(path.parent)
        os.rename(path, quarantine.held)
    except OSError:
        if not os.path.lexists(quarantine.held):
            quarantine.discard()
        raise
    sync_directory(directory)
    sync_directory(path.parent)
    return quarantine
