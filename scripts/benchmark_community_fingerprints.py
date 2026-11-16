"""Synthetic-only candidate experiment; this is not a production hash protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import struct
import tempfile
import time
from pathlib import Path

MIB = 1024 * 1024
CANDIDATES = {
    "edges-2x4m": (2, 4 * MIB),
    "spread-5x2m": (5, 2 * MIB),
    "spread-9x1m": (9, MIB),
}


def sample_ranges(size: int, candidate: str) -> tuple[tuple[int, int], ...]:
    """Return ordered disjoint (offset, length) ranges for an experimental layout."""
    if size < 0:
        raise ValueError("File size must be nonnegative")
    count, width = CANDIDATES[candidate]
    if size == 0:
        return ()
    width = min(width, size)
    ranges: list[tuple[int, int]] = []
    for index in range(count):
        offset = (size - width) * index // (count - 1)
        end = offset + width
        if ranges and offset <= sum(ranges[-1]):
            previous_offset, previous_length = ranges[-1]
            ranges[-1] = (previous_offset, max(previous_length, end - previous_offset))
        else:
            ranges.append((offset, width))
    return tuple(ranges)


def hash_candidate(path: Path, candidate: str) -> tuple[str, int]:
    """Measure a size/range-bound experimental SHA-256 input, or raw full SHA-256."""
    size = path.stat().st_size
    ranges = ((0, size),) if candidate == "full" else sample_ranges(size, candidate)
    digest = hashlib.sha256()
    if candidate != "full":
        digest.update(b"editarr-fingerprint-experiment\0" + candidate.encode("ascii") + b"\0")
        digest.update(struct.pack(">QI", size, len(ranges)))
    bytes_read = 0
    with path.open("rb") as stream:
        for offset, length in ranges:
            if candidate != "full":
                digest.update(struct.pack(">QQ", offset, length))
            stream.seek(offset)
            remaining = length
            while remaining:
                chunk = stream.read(min(remaining, MIB))
                if not chunk:
                    raise OSError("Synthetic input unexpectedly shortened")
                digest.update(chunk)
                bytes_read += len(chunk)
                remaining -= len(chunk)
    return digest.hexdigest(), bytes_read


def write_synthetic(path: Path, size: int) -> None:
    """Write allocated deterministic bytes, not sparse holes or user media."""
    block = bytes(range(256)) * (MIB // 256)
    with path.open("wb") as stream:
        remaining = size
        while remaining:
            length = min(remaining, len(block))
            stream.write(block[:length])
            remaining -= length


def benchmark(sizes: list[int], repeats: int) -> dict[str, object]:
    """Measure temporary synthetic files without claiming cold-cache performance."""
    if repeats < 2 or not sizes or any(size < 0 for size in sizes):
        raise ValueError("Use nonnegative sizes and at least two repeats")
    records: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="editarr-fingerprint-benchmark-") as directory:
        path = Path(directory) / "synthetic.bin"
        for size in sizes:
            write_synthetic(path, size)
            for candidate in (*CANDIDATES, "full"):
                walls: list[float] = []
                cpus: list[float] = []
                digests: set[str] = set()
                for _ in range(repeats):
                    cpu_start = time.process_time()
                    wall_start = time.perf_counter()
                    digest, bytes_read = hash_candidate(path, candidate)
                    walls.append((time.perf_counter() - wall_start) * 1000)
                    cpus.append((time.process_time() - cpu_start) * 1000)
                    digests.add(digest)
                if len(digests) != 1:
                    raise RuntimeError("Unstable synthetic digest")
                records.append(
                    {
                        "size_bytes": size,
                        "candidate": candidate,
                        "bytes_read": bytes_read,
                        "first_wall_ms": walls[0],
                        "repeat_median_wall_ms": statistics.median(walls[1:]),
                        "repeat_median_cpu_ms": statistics.median(cpus[1:]),
                        "digest": digest,
                    }
                )
    return {
        "experiment": "css35-candidates-1",
        "production_protocol": False,
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "repeats": repeats,
        "limits": (
            "Synthetic allocated files, freshly written/page-cache affected; "
            "no cold-cache, network storage or real-media claim."
        ),
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(benchmark([32 * 1024, 10 * MIB, 256 * MIB], args.repeats), indent=2))


if __name__ == "__main__":
    main()
