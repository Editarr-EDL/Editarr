"""Exact file generation evidence shared by catalog capture and scan execution."""

from collections.abc import Callable
from pathlib import Path

from scanner.integrations.community.fingerprints import FingerprintCache

_fingerprints = FingerprintCache()


def content_digest(
    path: str | Path, *, on_progress: Callable[[int, int], None] | None = None
) -> str:
    """Read the full SHA-256 digest, reusing unchanged file observations."""
    return _fingerprints.fingerprint(Path(path), full=True, on_progress=on_progress).digest


def cached_content_digest(path: str | Path) -> str | None:
    """Return a full digest only when this process already read the current file state."""
    result = _fingerprints.cached(Path(path), full=True)
    return result.digest if result is not None else None
