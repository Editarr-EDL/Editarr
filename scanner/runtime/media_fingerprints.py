"""Reuse exact file fingerprints across requests and worker restarts."""

import logging
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import md5, sha256
from pathlib import Path
from typing import BinaryIO

from scanner.integrations.community.fingerprints import FileChangedError, FingerprintCache
from scanner.settings.config import settings

from .durability import atomic_write_bytes
from .file_generation import file_generation, generation_matches
from .runtime_paths import ensure_user_private_directory

_fingerprints = FingerprintCache(track_full_reads=True)
_SHA256 = re.compile(rb"[0-9a-f]{64}")
_MD5 = re.compile(rb"[0-9a-f]{32}")
_MAX_WITNESS_BYTES = 512
_MD5_FULL_LIMIT = 50 * 1024 * 1024
_MD5_EDGE_BYTES = 10 * 1024 * 1024
_MD5_CHUNK_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SampledMediaFingerprint:
    """The legacy MD5 sample and the local generation from which it was read."""

    path: Path
    digest: str
    size_bytes: int
    generation: tuple[int, ...]


def sampled_media_read_size(size: int) -> int:
    """Return the number of media bytes the legacy MD5 scheme needs."""
    return 2 * _MD5_EDGE_BYTES if size > _MD5_FULL_LIMIT else size


def _require_sample_generation(
    requested: Path, resolved: Path, stream: BinaryIO, generation: tuple[int, ...]
) -> None:
    try:
        if (
            requested.resolve(strict=True) == resolved
            and file_generation(resolved.stat(), resolved) == generation
            and file_generation(os.fstat(stream.fileno()), resolved) == generation
        ):
            return
    except (OSError, RuntimeError):
        pass
    raise FileChangedError("The media file changed while reading its sampled fingerprint")


def sampled_media_fingerprint(
    path: str | Path, *, on_progress: Callable[[int, int], None] | None = None
) -> SampledMediaFingerprint:
    """Reuse an unchanged sample or read the legacy MD5 ranges from one stable file."""
    requested = Path(path)
    cached = cached_sampled_media_fingerprint(requested)
    if cached is not None:
        return cached
    measured = _read_sample(requested, on_progress)
    _remember_digest(measured.path, measured.generation, measured.digest, sampled=True)
    return measured


def _read_sample(
    requested: Path, on_progress: Callable[[int, int], None] | None
) -> SampledMediaFingerprint:
    try:
        resolved = requested.resolve(strict=True)
    except RuntimeError as exc:
        raise FileChangedError("The selected media path could not be resolved") from exc
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(resolved, flags | getattr(os, "O_BINARY", 0)), "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise FileChangedError("A sampled media fingerprint requires a regular file")
        generation = file_generation(metadata, resolved)
        _require_sample_generation(requested, resolved, stream, generation)
        size = metadata.st_size
        ranges = (
            ((0, _MD5_EDGE_BYTES), (size - _MD5_EDGE_BYTES, _MD5_EDGE_BYTES))
            if size > _MD5_FULL_LIMIT
            else ((0, size),)
        )
        total = sampled_media_read_size(size)
        done = 0
        digest = md5(usedforsecurity=False)
        if on_progress is not None:
            on_progress(done, total)
        for offset, length in ranges:
            stream.seek(offset)
            remaining = length
            while remaining:
                chunk = stream.read(min(_MD5_CHUNK_BYTES, remaining))
                if not chunk:
                    raise FileChangedError("The media file shortened during fingerprinting")
                digest.update(chunk)
                remaining -= len(chunk)
                done += len(chunk)
                if on_progress is not None:
                    on_progress(done, total)
        _require_sample_generation(requested, resolved, stream, generation)
        return SampledMediaFingerprint(resolved, digest.hexdigest(), size, generation)


def _witness_path(path: Path) -> Path:
    return settings.data_dir / "media-fingerprints" / sha256(os.fsencode(path)).hexdigest()


def _generation_prefix(generation: tuple[int, ...]) -> bytes:
    return ("v2 " + " ".join(map(str, generation)) + "\n").encode("ascii")


def _private(metadata: os.stat_result) -> bool:
    return os.name != "posix" or (metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o077)


def remember_content_digest(path: str | Path, generation: tuple[int, ...], digest: str) -> None:
    """Save a server-verified full digest for the unchanged file generation only."""
    _remember_digest(path, generation, digest, sampled=False)


def _remember_digest(
    path: str | Path, generation: tuple[int, ...], digest: str, *, sampled: bool
) -> None:
    try:
        path = Path(path).resolve(strict=True)
        if (
            not generation[1]
            or not stat.S_ISREG(generation[-1])
            or file_generation(path.stat(), path) != generation
        ):
            return
        encoded = digest.encode("ascii")
        pattern = _MD5 if sampled else _SHA256
        if not pattern.fullmatch(encoded):
            return
        witness = _witness_path(path)
        if sampled:
            witness = witness.with_suffix(".md5")
        if witness.parent.is_symlink():
            return
        directory = ensure_user_private_directory(witness.parent)
        metadata = directory.lstat()
        if stat.S_ISDIR(metadata.st_mode) and _private(metadata):
            atomic_write_bytes(
                witness, _generation_prefix(generation) + encoded, prefix=".sha-", sync_dir=True
            )
    except (OSError, RuntimeError, ValueError):
        # The optional cache never prevents the already verified operation from finishing.
        logger.debug("Could not save the verified file fingerprint cache entry")


def _persisted_digest(path: Path, *, sampled: bool) -> tuple[Path, tuple[int, ...], str] | None:
    try:
        resolved = path.resolve(strict=True)
        generation = file_generation(resolved.stat(), resolved)
        if not generation[1] or not stat.S_ISREG(generation[-1]):
            return None
        witness = _witness_path(resolved)
        if sampled:
            witness = witness.with_suffix(".md5")
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
        if not sampled and version == b"v1":
            legacy = tuple(map(int, stamp.split()))
            matches = generation_matches(legacy, generation)
        else:
            matches = data.startswith(_generation_prefix(generation))
        if (
            matches
            and (_MD5 if sampled else _SHA256).fullmatch(digest)
            and file_generation(path.stat(), path) == generation
        ):
            return resolved, generation, digest.decode("ascii")
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def cached_sampled_media_fingerprint(path: str | Path) -> SampledMediaFingerprint | None:
    """Return a previously read MD5 sample only for the current file generation."""
    result = _persisted_digest(Path(path), sampled=True)
    if result is None:
        return None
    resolved, generation, digest = result
    return SampledMediaFingerprint(resolved, digest, generation[2], generation)


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
    persisted = _persisted_digest(selected, sampled=False)
    return persisted[2] if persisted is not None else None


def active_digest_progress(generation: tuple[int, ...]) -> tuple[int, int] | None:
    """Share the current read position with approvals of the same file generation."""
    return _fingerprints.full_read_progress(generation)
