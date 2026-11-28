"""Idempotent ffmpeg frame extraction into the shared `_results/frames` cache."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .paths import (
    Layout,
    cached_frame_ok,
    ensure_allowed,
    has_symlink_below,
    matches_prefix,
    write_json,
)
from .tonemap import NO_HDR_PATH, hdr_options, select_hdr_path

log = logging.getLogger(__name__)

FRAME_TIMES = "frame_times.json"
LONG_EDGE = 768
JPEG_QUALITY = 4
# Downscale so the long edge is at most 768 px; never upscale small sources.
_SCALE = (
    f"scale='if(gte(iw,ih),min({LONG_EDGE},iw),-2)':'if(gte(iw,ih),-2,min({LONG_EDGE},ih))'"
)


_PTS_TIME = re.compile(r"pts_time:\s*(-?[\d.]+)")


def probe_start_time(source: Path) -> float:
    """The container's start time in seconds (input seeks are relative to it); 0 if unknown."""
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0",
        str(source),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        return float(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 0.0


def _ffmpeg(
    source: Path,
    target: Path,
    seconds: float,
    accurate: bool,
    hdr_path: str | None = None,
    not_before: float | None = None,
) -> tuple[bool, float | None]:
    """Extract one frame; return (ok, decoded frame's source timestamp or None).

    Input seeks (`accurate=False`) keep source timestamps (`-copyts`), skip frames before
    the source timestamp `not_before`, and log the decoded frame's pts through `showinfo`,
    because some containers (e.g. MPEG-TS) land seconds away from the requested time.
    Output seeks decode from the start and report no time.
    """
    seek = ["-ss", f"{seconds:.3f}"]
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info", "-y"]
    video_filter = _SCALE
    if hdr_path:
        hdr_input, video_filter = hdr_options(hdr_path, _SCALE, LONG_EDGE)
        command += hdr_input
    if accurate:
        command += ["-i", str(source)] + seek
    else:
        command += ["-copyts"] + seek + ["-i", str(source)]
        if not_before is not None:
            video_filter = f"select=gte(t\\,{not_before:.3f}),{video_filter}"
        video_filter += ",showinfo"
    command += ["-frames:v", "1", "-vf", video_filter, "-q:v", str(JPEG_QUALITY), "-f", "image2"]
    command += [str(target)]
    # A partial file left by an interrupted or failed attempt must never pass as this frame.
    target.unlink(missing_ok=True)
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        target.unlink(missing_ok=True)
        return False, None
    if result.returncode != 0 or not _cached(target):
        return False, None
    if accurate:
        return True, None
    match = _PTS_TIME.search((result.stderr or b"").decode("utf-8", "replace"))
    return True, (float(match.group(1)) if match else None)


def _cached(target: Path) -> bool:
    return target.is_file() and target.stat().st_size > 0


def _attempts(item: dict[str, Any], t: float) -> list[tuple[float, float, bool]]:
    """(seek, wanted time, accurate) tries: fast (input) seek first, then fallbacks.

    A film window's frame must come from that window, or it would be scored against the
    wrong labels: windows use only input seeks, whose decoded time is checked, retried from
    earlier seek points (a seek can overshoot by a keyframe interval in some containers),
    and never fall back to the first frame.
    """
    if "window" in item:
        early = float(item["window"][0])
        return [(t, t, False), (max(0.0, t - 10.0), t, False), (max(0.0, t - 60.0), t, False),
                (max(early, t - 1.0), max(early, t - 1.0), False)]
    return [(t, t, False), (t, t, True), (max(0.0, t - 1.0), max(0.0, t - 1.0), True),
            (0.0, 0.0, True)]


def _extract_frame(
    source: Path, partial: Path, item: dict[str, Any], t: float,
    hdr_path: str | None, start: float,
) -> tuple[float | None, str | None]:
    """Try each seek; return (actual time or None, None) on success, else (None, error)."""
    window = item.get("window")
    error = "could not be decoded"
    for seconds, wanted, accurate in _attempts(item, t):
        ok, pts = _ffmpeg(source, partial, seconds, accurate, hdr_path, start + wanted)
        if not ok:
            continue
        actual = None if pts is None else round(pts - start, 3)
        if window is None:
            return actual, None
        if actual is None:
            error = "decoded, but its timestamp could not be read"
        elif not float(window[0]) <= actual < float(window[1]):
            error = f"decoded at {actual:.3f}s, outside window {window[0]}-{window[1]}s"
        else:
            return actual, None
        partial.unlink(missing_ok=True)
    return None, error


def extract_item(
    data_root: str, item: dict[str, Any], hdr_path: str | None = None
) -> tuple[str, int, list[str], dict[str, float]]:
    """Extract missing frames for one item; return (id, newly written, errors, times).

    `hdr_path` is the tone-mapping path from `select_hdr_path()`, required for HDR items.
    `times` maps frame numbers to the decoded frame's actual time in seconds, when known.
    """
    root = Path(data_root)
    ensure_allowed(item["path"], root)
    source = root / item["path"]
    results = root / "_results"
    written = 0
    errors: list[str] = []
    times: dict[str, float] = {}
    missing = [frame for frame in item["frames"] if not cached_frame_ok(results, frame["file"])]
    if missing and item.get("hdr") and hdr_path is None:
        return item["id"], 0, [NO_HDR_PATH], times
    item_hdr_path = hdr_path if item.get("hdr") else None
    start = probe_start_time(source) if missing else 0.0
    for frame in missing:
        target = results / frame["file"]
        # Never write through, or keep, a symlink in the frames cache.
        for link in (target, target.parent):
            if link.is_symlink():
                link.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        if has_symlink_below(results, target.parent):
            errors.append(f"frame {frame['n']}: frame folder is linked outside the cache")
            continue
        partial = target.with_name(f"{target.stem}.part.jpg")
        t = float(frame["t"])
        actual, error = _extract_frame(source, partial, item, t, item_hdr_path, start)
        if error is None:
            partial.replace(target)
            written += 1
            if actual is not None:
                times[str(frame["n"])] = actual
        else:
            partial.unlink(missing_ok=True)
            errors.append(f"frame {frame['n']} at {t:.3f}s {error}")
    return item["id"], written, errors, times


def _record_times(layout: Layout, times: dict[str, dict[str, float]]) -> None:
    """Merge decoded frame times (item id → frame n → seconds) into `frame_times.json`."""
    path = layout.results / FRAME_TIMES
    recorded = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    for identifier, item_times in times.items():
        recorded.setdefault(identifier, {}).update(item_times)
    if times or path.is_file():
        write_json(path, recorded)


def frames_present(layout: Layout, item: dict[str, Any]) -> list[dict[str, Any]]:
    """Frames cached as genuine files (see `cached_frame_ok`); others count as missing."""
    return [frame for frame in item["frames"] if cached_frame_ok(layout.results, frame["file"])]


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
    times: dict[str, dict[str, float]] = {}
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
                identifier, written, item_errors, item_times = future.result()
            except Exception as exc:  # noqa: BLE001
                identifier, written, item_errors = item["id"], 0, [f"{type(exc).__name__}: {exc}"]
                item_times = {}
            written_total += written
            if item_times:
                times[identifier] = item_times
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
    _record_times(layout, times)
    if errors:
        log.warning("%d items had undecodable frames; see %s", len(errors), error_file)
    return errors
