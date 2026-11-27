"""Score pending items with a backend: one at a time, in parallel, or as one batch."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any

from .backends import Backend, BackendUnavailable
from .paths import Layout, ensure_allowed
from .results import aggregate

log = logging.getLogger(__name__)
# Optional per-frame backend outputs copied into result lines.
FRAME_EXTRAS = (
    "labels",
    "asked",
    "transcript",
    "severity",
    "severity_expected",
    "description",
    "describe",
    "logprob_fallback",
)
# Extras kept even when empty, so an empty list still records "measured, none".
KEEP_EMPTY = {"logprob_fallback"}


def score_item(
    backend: Backend,
    layout: Layout,
    item: dict[str, Any],
    frames: list[dict[str, Any]],
    thresholds: dict[str, float],
    previous: dict[str, Any] | None,
    batch: tuple[list[dict[str, Any]], float] | None = None,
) -> dict[str, Any]:
    """Score `frames` and return a line holding them plus any earlier ok frames.

    `batch` = (outputs, elapsed ms) when a batch backend already scored this item.
    """
    ensure_allowed(item["path"])
    started = time.perf_counter()
    record: dict[str, Any] = {"id": item["id"], "model": backend.model_id}
    runtime = getattr(backend, "runtime", None)
    if runtime:
        record["runtime"] = runtime
    try:
        if batch is not None:
            outputs = batch[0]
        else:
            outputs = backend.score_frames([layout.results / frame["file"] for frame in frames])
        if len(outputs) != len(frames):
            raise RuntimeError(f"backend returned {len(outputs)} results for {len(frames)} frames")
        by_n = {}
        if previous and previous.get("status") == "ok":
            by_n = {frame["n"]: frame for frame in previous.get("frames", [])}
        for frame, output in zip(frames, outputs, strict=True):
            entry = {"n": frame["n"], "scores": output.get("scores", {})}
            for extra in FRAME_EXTRAS:
                if output.get(extra) is not None and (output[extra] != [] or extra in KEEP_EMPTY):
                    entry[extra] = output[extra]
            by_n[frame["n"]] = entry
        frame_records = [by_n[n] for n in sorted(by_n)]
        scores, flagged = aggregate(frame_records, thresholds)
        record.update(status="ok", frames=frame_records, scores=scores, flagged=flagged)
        record["scored_n"] = [frame["n"] for frame in frames]
    except BackendUnavailable:
        raise  # The whole model is unusable: abort the run rather than log per-item errors.
    except Exception as exc:  # noqa: BLE001 - per-item failures are recorded, not fatal
        record.update(status="error", error=f"{type(exc).__name__}: {exc}", frames=[])
        record.update(scores={}, flagged=[])
    elapsed = batch[1] if batch is not None else (time.perf_counter() - started) * 1000
    record["elapsed_ms"] = round(elapsed, 1)
    return record


def scored(
    backend: Backend,
    layout: Layout,
    pending: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    thresholds: dict[str, float],
    done: dict[str, dict[str, Any]],
    concurrency: int,
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]:
    """Yield (item, frames, record) as items finish, scoring up to `concurrency` at once.

    Only the caller writes, so the jsonl keeps one writer; a BackendUnavailable from any
    worker stops new submissions and propagates.
    """
    if hasattr(backend, "score_items"):
        yield from _scored_batch(backend, layout, pending, thresholds, done)
        return
    if concurrency <= 1:
        for item, frames in pending:
            yield (
                item,
                frames,
                score_item(backend, layout, item, frames, thresholds, done.get(item["id"])),
            )
        return
    queue = iter(pending)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        running: dict[Future, tuple[dict[str, Any], list[dict[str, Any]]]] = {}

        def submit() -> None:
            for item, frames in queue:
                future = pool.submit(
                    score_item, backend, layout, item, frames, thresholds, done.get(item["id"])
                )
                running[future] = (item, frames)
                if len(running) >= concurrency:
                    return

        submit()
        try:
            while running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in finished:
                    item, frames = running.pop(future)
                    yield item, frames, future.result()
                submit()
        finally:
            for future in running:
                future.cancel()


def _scored_batch(backend, layout, pending, thresholds, done):
    """Batch backends (e.g. the cascade) score every pending item in one call; each line
    gets an equal share of the batch's wall time."""
    paths = [[layout.results / frame["file"] for frame in frames] for _item, frames in pending]
    started = time.perf_counter()
    try:
        outputs = backend.score_items(paths)
        error = None
    except BackendUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - recorded per item like single-item failures
        outputs, error = None, exc
    share = (time.perf_counter() - started) * 1000 / max(1, len(pending))
    for index, (item, frames) in enumerate(pending):
        if error is not None:
            record = {
                "id": item["id"],
                "model": backend.model_id,
                "status": "error",
                "error": f"{type(error).__name__}: {error}",
                "frames": [],
                "scores": {},
                "flagged": [],
                "elapsed_ms": round(share, 1),
            }
        else:
            record = score_item(
                backend,
                layout,
                item,
                frames,
                thresholds,
                done.get(item["id"]),
                batch=(outputs[index], share),
            )
        yield item, frames, record


def resolve_concurrency(backend: Backend, requested: int | None) -> int:
    """Requested (or the backend's default) parallel items; only backends that declare a
    `default_concurrency` (decision models) are thread-safe, others always run one at a time."""
    default = getattr(backend, "default_concurrency", None)
    if default is None:
        if requested and requested > 1:
            log.warning(
                "%s does not support --concurrency; scoring one item at a time", backend.model_id
            )
        return 1
    return max(1, requested or default)
