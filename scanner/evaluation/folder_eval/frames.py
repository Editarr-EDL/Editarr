"""Idempotent ffmpeg frame extraction into the shared `_results/frames` cache."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .paths import Layout, ensure_allowed, matches_prefix, write_json
from .tonemap import NO_HDR_PATH, hdr_options, select_hdr_path

log = logging.getLogger(__name__)

LONG_EDGE = 768
JPEG_QUALITY = 4
# Downscale so the long edge is at most 768 px; never upscale small sources.
_SCALE = (
    f"scale='if(gte(iw,ih),min({LONG_EDGE},iw),-2)':'if(gte(iw,ih),-2,min({LONG_EDGE},ih))'"
)


def _ffmpeg(
    source: Path, target: Path, seconds: float, accurate: bool, hdr_path: str | None = None
) -> bool:
    seek = ["-ss", f"{seconds:.3f}"]
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    video_filter = _SCALE
    if hdr_path:
        hdr_input, video_filter = hdr_options(hdr_path, _SCALE, LONG_EDGE)
        command += hdr_input
    command += (["-i", str(source)] + seek) if accurate else (seek + ["-i", str(source)])
    command += ["-frames:v", "1", "-vf", video_filter, "-q:v", str(JPEG_QUALITY), "-f", "image2"]
    command += [str(target)]
    # A partial file left by an interrupted or failed attempt must never pass as this frame.
    target.unlink(missing_ok=True)
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        return False
    return result.returncode == 0 and _cached(target)


def _cached(target: Path) -> bool:
    return target.is_file() and target.stat().st_size > 0


def _attempts(item: dict[str, Any], t: float) -> list[tuple[float, bool]]:
    """Fast seek first, then decode-accurate and slightly earlier seeks.

    Only whole clips fall back to the first frame: a film window's frame must come from
    that window, or it would be scored against the wrong labels.
    """
    attempts = [(t, False), (t, True), (max(0.0, t - 1.0), True)]
    if "window" not in item:
        attempts.append((0.0, True))
    return attempts


def extract_item(
    data_root: str, item: dict[str, Any], hdr_path: str | None = None
) -> tuple[str, int, list[str]]:
    """Extract missing frames for one item; return (id, newly written, errors).

    `hdr_path` is the tone-mapping path from `select_hdr_path()`, required for HDR items.
    """
    root = Path(data_root)
    ensure_allowed(item["path"], root)
    source = root / item["path"]
    results = root / "_results"
    written = 0
    errors: list[str] = []
    missing = [frame for frame in item["frames"] if not _cached(results / frame["file"])]
    if missing and item.get("hdr") and hdr_path is None:
        return item["id"], 0, [NO_HDR_PATH]
    item_hdr_path = hdr_path if item.get("hdr") else None
    for frame in missing:
        target = results / frame["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.stem}.part.jpg")
        t = float(frame["t"])
        if any(
            _ffmpeg(source, partial, seconds, accurate, item_hdr_path)
            for seconds, accurate in _attempts(item, t)
        ):
            partial.replace(target)
            written += 1
        else:
            partial.unlink(missing_ok=True)
            errors.append(f"frame {frame['n']} at {t:.3f}s could not be decoded")
    return item["id"], written, errors


def frames_present(layout: Layout, item: dict[str, Any]) -> list[dict[str, Any]]:
    return [frame for frame in item["frames"] if (layout.results / frame["file"]).is_file()]


def extract_frames(
    layout: Layout,
    *,
    only_folder: str | None = None,
    limit: int | None = None,
    workers: int = 4,
) -> dict[str, list[str]]:
    """Extract frames for inventory items in parallel; already complete items are skipped."""
    inventory = layout.load_inventory()
    selected = [item for item in inventory["items"] if matches_prefix(item["path"], only_folder)]
    pending = [item for item in selected if len(frames_present(layout, item)) < len(item["frames"])]
    complete = len(selected) - len(pending)
    if limit is not None:
        pending = pending[:limit]
    hdr_path = select_hdr_path() if any(item.get("hdr") for item in pending) else None
    log.info(
        "%d items selected, %d already complete, %d to extract with %d workers",
        len(selected), complete, len(pending), workers,
    )
    errors: dict[str, list[str]] = {}
    started = time.monotonic()
    written_total = 0
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(extract_item, str(layout.data_root), item, hdr_path): item
            for item in pending
        }
        for done, future in enumerate(as_completed(futures), start=1):
            item = futures[future]
            # A refused (PermissionError) or crashed item is recorded, never aborting the batch.
            try:
                identifier, written, item_errors = future.result()
            except Exception as exc:  # noqa: BLE001
                identifier, written, item_errors = item["id"], 0, [f"{type(exc).__name__}: {exc}"]
            written_total += written
            if item_errors:
                errors[identifier] = item_errors
            if done % 25 == 0 or done == len(futures):
                elapsed = time.monotonic() - started
                eta = elapsed / done * (len(futures) - done)
                log.info(
                    "frames %d/%d items (%d frames, %d failed items) eta %.0fs",
                    done, len(futures), written_total, len(errors), eta,
                )
    error_file = layout.results / "frame_errors.json"
    previous = {}
    if error_file.is_file():
        previous = json.loads(error_file.read_text(encoding="utf-8"))
    for item in pending:
        previous.pop(item["id"], None)
    previous.update(errors)
    write_json(error_file, previous)
    if errors:
        log.warning("%d items had undecodable frames; see %s", len(errors), error_file)
    return errors
