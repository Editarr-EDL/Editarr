"""Expose active verification reads without creating scan records."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import Lock
from uuid import uuid4

from .media_fingerprints import active_digest_progress


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
_activities: dict[str, tuple[tuple[int, int], tuple[int, ...] | None, FingerprintActivity]] = {}


@contextmanager
def track_fingerprint(
    *,
    actor: tuple[int, int],
    item_name: str,
    version_name: str | None,
    generation: tuple[int, ...],
    item_id: str,
    media_source_id: str | None,
    source_scan_id: str,
    sampled_total: int | None = None,
) -> Iterator[Callable[[int, int], None]]:
    """Publish one authorized read until it finishes or fails."""
    item = FingerprintActivity(
        uuid4().hex,
        item_name,
        version_name,
        0,
        generation[2] if sampled_total is None else sampled_total,
        item_id,
        media_source_id,
        source_scan_id,
    )
    shared_generation = generation if sampled_total is None else None
    with _lock:
        _activities[item.id] = (actor, shared_generation, item)

    def report(done: int, total: int) -> None:
        current = replace(item, bytes_read=done, bytes_total=total, reading=True)
        with _lock:
            _activities[item.id] = (actor, shared_generation, current)

    try:
        yield report
    finally:
        with _lock:
            _activities.pop(item.id, None)


def active_fingerprints(actor: tuple[int, int]) -> list[FingerprintActivity]:
    """Return only work initiated under the caller's current authority."""
    with _lock:
        activities = [
            (generation, item) for owner, generation, item in _activities.values() if owner == actor
        ]
    results = []
    for generation, item in activities:
        progress = active_digest_progress(generation) if generation and not item.reading else None
        if progress is not None:
            item = replace(item, bytes_read=progress[0], bytes_total=progress[1], reading=True)
        results.append(item)
    return results
