"""Local file stability checks and bounded, process-local fingerprint reuse."""

from __future__ import annotations

import os
import stat
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import BinaryIO

from scanner.formats.community.fingerprints import (
    FULL_ALGORITHM,
    SAMPLED_ALGORITHM,
    digest_stream,
    sample_ranges,
)


class FileChangedError(OSError):
    """The selected file changed; discard the result and retry with stable bytes."""


@dataclass(frozen=True)
class FileFingerprint:
    algorithm: str
    digest: str
    size_bytes: int
    ranges: tuple[tuple[int, int], ...]
    measured_at: str


def _file_state(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
    )


def _check_state(path: Path, stream: BinaryIO, expected: tuple[int, ...]) -> None:
    try:
        opened = _file_state(os.fstat(stream.fileno()))
        selected = _file_state(path.stat())
    except OSError as error:
        raise FileChangedError("File disappeared during fingerprinting; retry") from error
    if opened != expected or selected != expected:
        raise FileChangedError("File changed during fingerprinting; retry")


class FingerprintCache:
    """Cache up to capacity file-state/algorithm results; full reads are explicit."""

    def __init__(self, capacity: int = 128) -> None:
        if capacity < 1:
            raise ValueError("Cache capacity must be positive")
        self._capacity = capacity
        self._entries: OrderedDict[tuple[tuple[int, ...], str], FileFingerprint] = OrderedDict()
        self._lock = RLock()

    def fingerprint(self, path: Path, *, full: bool = False) -> FileFingerprint:
        """Return stable evidence or raise; never silently fall back to a stale hash."""
        path = Path(path)
        algorithm = FULL_ALGORITHM if full else SAMPLED_ALGORITHM
        with self._lock:
            try:
                with path.open("rb") as stream:
                    state = _file_state(os.fstat(stream.fileno()))
                    if not stat.S_ISREG(state[-1]):
                        raise ValueError("Fingerprinting requires a regular file")
                    _check_state(path, stream, state)
                    identity = state[:2]
                    for old_key in list(self._entries):
                        if old_key[0][:2] == identity and old_key[0] != state:
                            del self._entries[old_key]
                    key = (state, algorithm)
                    cached = self._entries.get(key) if state[1] else None
                    if cached is not None:
                        _check_state(path, stream, state)
                        self._entries.move_to_end(key)
                        return cached
                    try:
                        digest = digest_stream(stream, state[2], algorithm)
                    except OSError as error:
                        raise FileChangedError("File could not be read stably; retry") from error
                    _check_state(path, stream, state)
                    result = FileFingerprint(
                        algorithm=algorithm,
                        digest=digest,
                        size_bytes=state[2],
                        ranges=sample_ranges(state[2]) if not full else (),
                        measured_at=datetime.now(UTC).isoformat(),
                    )
                    if state[1]:
                        self._entries[key] = result
                        self._entries.move_to_end(key)
                        while len(self._entries) > self._capacity:
                            self._entries.popitem(last=False)
                    return result
            except (OSError, ValueError):
                self._entries.clear()
                raise
