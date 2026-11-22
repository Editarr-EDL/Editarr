"""Reuse exact file fingerprints across requests and worker restarts."""

import logging
import os
import re
import stat
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path

from scanner.integrations.community.fingerprints import FingerprintCache
from scanner.settings.config import settings

from .durability import atomic_write_bytes
from .file_generation import file_generation, generation_matches
from .runtime_paths import ensure_user_private_directory

_fingerprints = FingerprintCache(track_full_reads=True)
_SHA256 = re.compile(rb"[0-9a-f]{64}")
_MAX_WITNESS_BYTES = 512
logger = logging.getLogger(__name__)


def _witness_path(path: Path) -> Path:
    return settings.data_dir / "media-fingerprints" / sha256(os.fsencode(path)).hexdigest()


def _generation_prefix(generation: tuple[int, ...]) -> bytes:
    return ("v2 " + " ".join(map(str, generation)) + "\n").encode("ascii")


def _private(metadata: os.stat_result) -> bool:
    return os.name != "posix" or (metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o077)


def remember_content_digest(path: str | Path, generation: tuple[int, ...], digest: str) -> None:
    """Save a server-verified full digest for the unchanged file generation only."""
    try:
        path = Path(path).resolve(strict=True)
        if (
            not generation[1]
            or not stat.S_ISREG(generation[-1])
            or file_generation(path.stat(), path) != generation
        ):
            return
        encoded = digest.encode("ascii")
        if not _SHA256.fullmatch(encoded):
            return
        witness = _witness_path(path)
        if witness.parent.is_symlink():
            return
        directory = ensure_user_private_directory(witness.parent)
        metadata = directory.lstat()
        if stat.S_ISDIR(metadata.st_mode) and _private(metadata):
            atomic_write_bytes(
                witness, _generation_prefix(generation) + encoded, prefix=".sha-", sync_dir=True
            )
    except (OSError, ValueError):
        # The optional cache never prevents the already verified operation from finishing.
        logger.debug("Could not save the verified file fingerprint cache entry")


def _persisted_content_digest(path: Path) -> str | None:
    try:
        resolved = path.resolve(strict=True)
        generation = file_generation(resolved.stat(), resolved)
        if not generation[1] or not stat.S_ISREG(generation[-1]):
            return None
        witness = _witness_path(resolved)
        directory = witness.parent.lstat()
        record = witness.lstat()
        if (
            not stat.S_ISDIR(directory.st_mode)
            or not stat.S_ISREG(record.st_mode)
            or not _private(directory)
            or not _private(record)
            or record.st_size > _MAX_WITNESS_BYTES
        ):
            return None
        with witness.open("rb") as stream:
            data = stream.read(_MAX_WITNESS_BYTES + 1)
        if len(data) != record.st_size:
            return None
        header, _, digest = data.partition(b"\n")
        version, _, stamp = header.partition(b" ")
        if version == b"v1":
            legacy = tuple(map(int, stamp.split()))
            matches = generation_matches(legacy, generation)
        else:
            matches = data.startswith(_generation_prefix(generation))
        if (
            matches
            and _SHA256.fullmatch(digest)
            and file_generation(path.stat(), path) == generation
        ):
            return digest.decode("ascii")
    except (OSError, ValueError):
        pass
    return None


def content_digest(
    path: str | Path, *, on_progress: Callable[[int, int], None] | None = None
) -> str:
    """Read the full SHA-256 digest, reusing unchanged file observations."""
    selected = Path(path)
    saved = cached_content_digest(selected)
    if saved is not None:
        return saved
    exact = _fingerprints.fingerprint(selected, full=True, on_progress=on_progress)
    remember_content_digest(selected, exact.generation, exact.digest)
    return exact.digest


def cached_content_digest(path: str | Path) -> str | None:
    """Return only a full digest previously verified for the current generation."""
    selected = Path(path)
    result = _fingerprints.cached(selected, full=True)
    if result is not None:
        try:
            if file_generation(selected.stat(), selected) == result.generation:
                return result.digest
        except OSError:
            return None
    return _persisted_content_digest(selected)


def active_digest_progress(generation: tuple[int, ...]) -> tuple[int, int] | None:
    """Share the current read position with approvals of the same file generation."""
    return _fingerprints.full_read_progress(generation)
