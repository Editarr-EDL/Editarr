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

log = logging.getLogger(__name__)

LONG_EDGE = 768
JPEG_QUALITY = 4
# Downscale so the long edge is at most 768 px; never upscale small sources.
_SCALE = (
    f"scale='if(gte(iw,ih),min({LONG_EDGE},iw),-2)':'if(gte(iw,ih),-2,min({LONG_EDGE},ih))'"
)


# HDR (PQ/HLG) sources: VideoToolbox decodes and tone-maps to BT.709 SDR; without it
# frames come out washed out. Assumes landscape sources (films).
_HDR_INPUT = ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"]
_HDR_FILTER = (
    f"scale_vt=w='min({LONG_EDGE},iw)':h=-2:color_matrix=bt709:color_primaries=bt709:"
    "color_transfer=bt709,hwdownload,format=p010le,format=yuv420p"
)


def _ffmpeg(source: Path, target: Path, seconds: float, accurate: bool, hdr: bool = False) -> bool:
    seek = ["-ss", f"{seconds:.3f}"]
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if hdr:
        command += _HDR_INPUT
    command += (["-i", str(source)] + seek) if accurate else (seek + ["-i", str(source)])
    video_filter = _HDR_FILTER if hdr else _SCALE
    command += ["-frames:v", "1", "-vf", video_filter, "-q:v", str(JPEG_QUALITY), "-f", "image2"]
    command += [str(target)]
    try:
        subprocess.run(command, capture_output=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return False
    return target.is_file() and target.stat().st_size > 0


def extract_item(data_root: str, item: dict[str, Any]) -> tuple[str, int, list[str]]:
    """Extract missing frames for one item; return (id, newly written, errors)."""
    root = Path(data_root)
    ensure_allowed(item["path"], root)
    source = root / item["path"]
    results = root / "_results"
    written = 0
    errors: list[str] = []
    for frame in item["frames"]:
        target = results / frame["file"]
        if target.is_file() and target.stat().st_size > 0:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.stem}.part.jpg")
        t = float(frame["t"])
        # Fast seek first; fall back to decode-accurate and slightly earlier seeks.
        attempts = [(t, False), (t, True), (max(0.0, t - 1.0), True), (0.0, True)]
        if item.get("hdr"):
            # No software fallback: untone-mapped HDR frames would silently skew scores.
            attempts = [(t, False, True), (t, True, True), (max(0.0, t - 1.0), True, True)]
        else:
            attempts = [(*attempt, False) for attempt in attempts]
        if any(_ffmpeg(source, partial, *attempt) for attempt in attempts):
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
    if limit is not None:
        selected = selected[:limit]
    pending = [item for item in selected if len(frames_present(layout, item)) < len(item["frames"])]
    log.info(
        "%d items selected, %d already complete, %d to extract with %d workers",
        len(selected), len(selected) - len(pending), len(pending), workers,
    )
    errors: dict[str, list[str]] = {}
    started = time.monotonic()
    written_total = 0
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(extract_item, str(layout.data_root), item) for item in pending
        ]
        for done, future in enumerate(as_completed(futures), start=1):
            identifier, written, item_errors = future.result()
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
