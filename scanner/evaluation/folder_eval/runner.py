"""Resumable model runs over cached frames, appending one JSON line per item."""

from __future__ import annotations

import logging
import os
import time
from contextlib import closing
from typing import Any

from .backends import BackendUnavailable, create_backend
from .frames import frames_present
from .paths import Layout, matches_prefix, open_append
from .results import append_line, load_results, write_model_index
from .run_config import FIELD as CONFIG_FIELD
from .run_config import archive_results, check_resume, fingerprint, scoring_config
from .run_meta import process_peak_rss_mb, server_peak_rss_mb, write_meta
from .scoring import resolve_concurrency, scored

log = logging.getLogger(__name__)


def _unavailable_error(record: dict[str, Any]) -> bool:
    """Errors from older runs that recorded an unavailable backend per item are retried."""
    error = record.get("error") or ""
    return error.startswith("BackendUnavailable") or "unavailable:" in error


def _plan(
    layout: Layout,
    items: list[dict[str, Any]],
    done: dict[str, dict[str, Any]],
    subset: dict[str, list[int]] | None,
    retry_errors: bool,
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], int, int]:
    """Return (item, frames still to score) pairs plus done and frameless item counts."""
    pending, already, no_frames = [], 0, 0
    for item in items:
        record = done.get(item["id"])
        if (
            record
            and record.get("status") != "ok"
            and not retry_errors
            and not _unavailable_error(record)
        ):
            already += 1
            continue
        present = frames_present(layout, item)
        if subset is not None:
            present = [frame for frame in present if frame["n"] in subset[item["id"]]]
        if not present:
            no_frames += 1
            continue
        scored = set()
        if record and record.get("status") == "ok":
            scored = {frame["n"] for frame in record.get("frames", [])}
        missing = [frame for frame in present if frame["n"] not in scored]
        if missing:
            pending.append((item, missing))
        else:
            already += 1
    return pending, already, no_frames


def run_model(
    layout: Layout,
    model_id: str,
    *,
    limit: int | None = None,
    only_folder: str | None = None,
    retry_errors: bool = False,
    subset: dict[str, list[int]] | None = None,
    concurrency: int | None = None,
    restart: bool = False,
) -> dict[str, Any]:
    """Score pending inventory items with one model, resuming from its jsonl.

    An item is done once its merged line has every wanted frame (all extracted frames,
    or only the `subset` frames); earlier ok frames are reused, not recomputed.
    `concurrency` scores that many items in parallel (decision backends only; default:
    the backend's `default_concurrency`). Model downloads follow
    `backends.configure_model_downloads`.

    Resuming requires every stored line to carry the current scoring-configuration
    fingerprint (see `run_config`), else ConfigMismatch; `restart` first archives the
    model's jsonl and meta.json and starts again.
    """
    inventory = layout.load_inventory()
    if restart:
        for archived in archive_results(layout, model_id):
            log.info("%s: archived %s", model_id, archived.name)
    output = layout.model_jsonl(model_id)
    done = load_results(output)
    selected = [
        item
        for item in inventory["items"]
        if matches_prefix(item["path"], only_folder) and (subset is None or item["id"] in subset)
    ]
    pending, already, no_frames = _plan(layout, selected, done, subset, retry_errors)
    if limit is not None:
        pending = pending[:limit]
    log.info(
        "%s: %d selected, %d already done, %d without frames (run `frames`), %d to score",
        model_id,
        len(selected),
        already,
        no_frames,
        len(pending),
    )
    if not pending:
        write_model_index(layout)
        return {"items": 0, "frames": 0, "errors": 0}
    load_started = time.perf_counter()
    backend = create_backend(model_id)
    try:
        config = scoring_config(backend)
        check_resume(layout.model_jsonl(model_id), model_id, fingerprint(config))
        return _score_pending(
            layout, backend, model_id, pending, done, concurrency,
            round((time.perf_counter() - load_started) * 1000, 1), config,
        )
    finally:
        # Every exit path (errors, BackendUnavailable, KeyboardInterrupt, SystemExit from
        # SIGTERM) releases the model before `all` loads the next one.
        backend.close()
        write_model_index(layout)


def _score_pending(
    layout: Layout,
    backend: Any,
    model_id: str,
    pending: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    done: dict[str, dict[str, Any]],
    concurrency: int | None,
    load_ms: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Score `pending` with a loaded backend, appending lines (each with the config
    fingerprint) and writing meta.json."""
    config_id = fingerprint(config)
    output = layout.model_jsonl(model_id)
    thresholds = backend.meta().get("thresholds", {})
    workers = resolve_concurrency(backend, concurrency)
    log.info("%s loaded in %.1fs, %d concurrent item(s)", model_id, load_ms / 1000, workers)
    layout.models.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    frame_count = errors = 0
    scoring_ms = 0.0
    last_log = started
    index = 0
    try:
        descriptor = open_append(output)
        # closing(): if the loop body fails (e.g. a write error) or is interrupted, the
        # generator's executor cancels queued items and joins running ones here, before
        # the caller closes the backend those threads still use.
        try:
            scoring = scored(backend, layout, pending, thresholds, done, workers)
            with closing(scoring) as results:
                for index, (item, frames, record) in enumerate(results, start=1):
                    record[CONFIG_FIELD] = config_id
                    append_line(descriptor, record)
                    if record["status"] == "ok":
                        frame_count += len(frames)
                        scoring_ms += record["elapsed_ms"]
                    else:
                        errors += 1
                        log.warning("%s %s: %s", model_id, item["path"], record["error"])
                    now = time.perf_counter()
                    if now - last_log >= 10 or index == len(pending):
                        last_log = now
                        eta = (now - started) / index * (len(pending) - index)
                        log.info(
                            "%s %d/%d items, %d frames, %d errors, %.1f ms/frame, eta %.0fs",
                            model_id,
                            index,
                            len(pending),
                            frame_count,
                            errors,
                            scoring_ms / frame_count if frame_count else 0.0,
                            eta,
                        )
        finally:
            os.close(descriptor)
    except BackendUnavailable:
        log.error("%s became unavailable; aborting after %d items", model_id, index - 1)
        raise
    finally:
        stats = {
            "items": len(pending),
            "frames": frame_count,
            "errors": errors,
            "load_ms": load_ms,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "ms_per_frame": round(scoring_ms / frame_count, 2) if frame_count else None,
            "concurrency": workers,
            "wall_ms_per_frame": (
                round((time.perf_counter() - started) * 1000 / frame_count, 2)
                if frame_count
                else None
            ),
            "process_peak_rss_mb": process_peak_rss_mb(),
            "server_peak_rss_mb": server_peak_rss_mb(backend),
        }
        if frame_count or errors:
            write_meta(layout, backend, stats, config)
    return stats
