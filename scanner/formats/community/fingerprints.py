"""Versioned byte contracts for sampled and optional full-file fingerprints."""

from __future__ import annotations

import hashlib
import struct
from typing import BinaryIO

SAMPLED_ALGORITHM = "editarr-community-file-sha256-sampled-1"
FULL_ALGORITHM = "sha256-full-1"
SAMPLE_BYTES = 1024 * 1024
SAMPLE_COUNT = 9
MAX_FILE_SIZE = (1 << 64) - 1


def sample_ranges(size: int) -> tuple[tuple[int, int], ...]:
    """Return merged ordered ranges, with a nine MiB maximum byte budget."""
    if not 0 <= size <= MAX_FILE_SIZE:
        raise ValueError("File size is outside the fingerprint contract")
    if not size:
        return ()
    width = min(size, SAMPLE_BYTES)
    ranges: list[tuple[int, int]] = []
    for index in range(SAMPLE_COUNT):
        offset = (size - width) * index // (SAMPLE_COUNT - 1)
        if ranges and offset <= sum(ranges[-1]):
            previous_offset, previous_length = ranges[-1]
            ranges[-1] = (previous_offset, max(previous_length, offset + width - previous_offset))
        else:
            ranges.append((offset, width))
    return tuple(ranges)


def digest_stream(stream: BinaryIO, size: int, algorithm: str) -> str:
    """Hash exact selected bytes; the caller must independently verify file stability."""
    ranges = sample_ranges(size)
    if algorithm not in (SAMPLED_ALGORITHM, FULL_ALGORITHM):
        raise ValueError("Unsupported fingerprint algorithm")
    digest = hashlib.sha256()
    if algorithm == SAMPLED_ALGORITHM:
        digest.update(SAMPLED_ALGORITHM.encode("ascii") + b"\0")
        digest.update(struct.pack(">QI", size, len(ranges)))
    else:
        ranges = ((0, size),) if size else ()
    for offset, length in ranges:
        if algorithm == SAMPLED_ALGORITHM:
            digest.update(struct.pack(">QQ", offset, length))
        stream.seek(offset)
        remaining = length
        while remaining:
            chunk = stream.read(min(remaining, SAMPLE_BYTES))
            if not chunk:
                raise OSError("File shortened during fingerprint read")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()
