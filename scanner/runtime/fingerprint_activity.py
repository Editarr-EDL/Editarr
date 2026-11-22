"""Expose active full-file verification reads without creating scan records."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import Lock
from uuid import uuid4


@dataclass(frozen=True)
class FingerprintActivity:
    id: str
    item_name: str
    version_name: str | None
    bytes_read: int
    bytes_total: int
    item_id: str
    media_source_id: str | None
    source_scan_id: str
    reading: bool = False


_lock = Lock()
_activities: dict[str, tuple[tuple[int, int], FingerprintActivity]] = {}


@contextmanager
def track_fingerprint(
    *,
    actor: tuple[int, int],
    item_name: str,
    version_name: str | None,
    size: int,
    item_id: str,
    media_source_id: str | None,
    source_scan_id: str,
) -> Iterator[Callable[[int, int], None]]:
    """Publish one authorized read until it finishes or fails."""
    item = FingerprintActivity(
        uuid4().hex, item_name, version_name, 0, size, item_id, media_source_id, source_scan_id
    )
    with _lock:
        _activities[item.id] = (actor, item)

    def report(done: int, total: int) -> None:
        current = replace(item, bytes_read=done, bytes_total=total, reading=True)
        with _lock:
            _activities[item.id] = (actor, current)

    try:
        yield report
    finally:
        with _lock:
            _activities.pop(item.id, None)


def active_fingerprints(actor: tuple[int, int]) -> list[FingerprintActivity]:
    """Return only work initiated under the caller's current authority."""
    with _lock:
        return [item for owner, item in _activities.values() if owner == actor]
