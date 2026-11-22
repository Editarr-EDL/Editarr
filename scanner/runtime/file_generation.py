"""Local media observations scoped to a canonical path."""

import os
from hashlib import sha256
from pathlib import Path


def file_generation(metadata: os.stat_result, path: str | Path) -> tuple[int, ...]:
    """Identify an unchanged file at the resolved path across remounts."""
    canonical = os.fsencode(os.path.realpath(path))
    # Negative values distinguish path signatures from legacy device numbers.
    path_signature = -1 - int.from_bytes(sha256(canonical).digest()[:16], "big")
    return (
        path_signature,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
    )


def generation_matches(saved: object, current: tuple[int, ...]) -> bool:
    """Compare observations already scoped to the same path, including legacy records."""
    if (
        not isinstance(saved, (tuple, list))
        or len(saved) != len(current)
        or any(type(value) is not int for value in saved)
    ):
        return False
    saved = tuple(saved)
    if saved[0] >= 0:
        return saved[1:] == current[1:]
    return saved == current
