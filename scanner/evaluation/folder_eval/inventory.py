"""Enumerate evaluation media, probe durations, attach labels, and plan frames."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .labels import FAMILY_TOKENS, LabelRules
from .paths import (
    MEDIA_ROOT,
    Layout,
    ensure_allowed,
    is_excluded,
    item_id,
    media_kind,
    resolves_into_excluded,
    write_json,
)

log = logging.getLogger(__name__)

SHORT_CLIP_SECONDS = 30.0
SHORT_CLIP_FRACTIONS = (0.125, 0.375, 0.625, 0.875)
LONG_CLIP_STEP_SECONDS = 15.0
MAX_FRAMES = 24


def iter_media(data_root: Path) -> Iterator[str]:
    """Yield data-root-relative media paths, pruning excluded folders before listing them.

    Symlinked directories are not followed; symlinked files resolving into an excluded
    folder are skipped.
    """
    stack = [MEDIA_ROOT]
    while stack:
        relative_dir = stack.pop()
        if is_excluded(relative_dir):
            continue
        with os.scandir(data_root / relative_dir) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            relative = f"{relative_dir}/{entry.name}"
            if is_excluded(relative):
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(relative)
            elif entry.is_file() and media_kind(entry.name):
                if entry.is_symlink() and resolves_into_excluded(data_root, relative):
                    continue
                yield relative


def probe_duration(path: Path) -> float | None:
    """Return the container (or first video stream) duration in seconds via ffprobe."""
    command = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=duration",
        "-select_streams", "v:0", "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    candidates = [payload.get("format", {}).get("duration")]
    candidates += [stream.get("duration") for stream in payload.get("streams", [])]
    for value in candidates:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return round(duration, 3)
    return None


def plan_frame_times(kind: str, duration_s: float | None) -> list[float]:
    """Frame timestamps: images/unknown → [0]; ≤30 s → 4 quartile midpoints; else every 15 s."""
    if kind == "image" or not duration_s:
        return [0.0]
    if duration_s <= SHORT_CLIP_SECONDS:
        return [round(duration_s * fraction, 3) for fraction in SHORT_CLIP_FRACTIONS]
    count = int(duration_s // LONG_CLIP_STEP_SECONDS)
    if duration_s % LONG_CLIP_STEP_SECONDS >= LONG_CLIP_STEP_SECONDS / 2:
        count += 1
    if count <= MAX_FRAMES:
        return [
            round(LONG_CLIP_STEP_SECONDS * index + LONG_CLIP_STEP_SECONDS / 2, 3)
            for index in range(count)
        ]
    step = duration_s / MAX_FRAMES
    return [round(step * index + step / 2, 3) for index in range(MAX_FRAMES)]


def _previous_items(layout: Layout) -> list[dict[str, Any]]:
    if not layout.inventory.is_file():
        return []
    try:
        payload = json.loads(layout.inventory.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload.get("items", [])


def build_inventory(
    data_root: Path,
    rules: LabelRules,
    *,
    probe: Callable[[Path], float | None] = probe_duration,
    previous: dict[str, float | None] | None = None,
    workers: int = 8,
) -> dict[str, Any]:
    """Build the inventory payload; durations cached in `previous` are reused."""
    previous = previous or {}
    paths = sorted(iter_media(data_root))
    to_probe = [
        path for path in paths
        if media_kind(path) == "video" and previous.get(path) is None
    ]
    for path in to_probe:
        ensure_allowed(path, data_root)
    log.info("Found %d media files; probing %d durations", len(paths), len(to_probe))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        probed = dict(zip(to_probe, pool.map(lambda p: probe(data_root / p), to_probe)))

    items = []
    for path in paths:
        kind = media_kind(path)
        duration = None if kind == "image" else probed.get(path, previous.get(path))
        label = rules.label(path)
        item_key = item_id(path)
        frames = [
            {"n": index, "t": t, "file": f"frames/{item_key}/{index}.jpg"}
            for index, t in enumerate(plan_frame_times(kind, duration))
        ]
        entry: dict[str, Any] = {
            "id": item_key,
            "path": path,
            "folder": path.rsplit("/", 1)[0],
            "kind": kind,
            "duration_s": duration,
            "expected": label.expected,
            "scored": label.scored,
            "rule": label.rule,
            "frames": frames,
        }
        if label.notes:
            entry["notes"] = label.notes
        items.append(entry)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "families": FAMILY_TOKENS,
        "items": items,
    }


def top_folder(path: str) -> str:
    return "/".join(path.split("/")[:2])


def write_inventory(layout: Layout, rules: LabelRules) -> dict[str, Any]:
    previous = _previous_items(layout)
    durations = {item["path"]: item.get("duration_s") for item in previous if "window" not in item}
    payload = build_inventory(layout.data_root, rules, previous=durations)
    # Film windows come from the `films` subcommand; keep them across re-inventories.
    payload["items"] += [item for item in previous if "window" in item]
    write_json(layout.inventory, payload)
    counts: dict[str, dict[str, int]] = {}
    for item in payload["items"]:
        bucket = counts.setdefault(top_folder(item["path"]), {})
        label = ",".join(item["expected"]) or "clean"
        if not item["scored"]:
            label += " (unscored)"
        bucket[label] = bucket.get(label, 0) + 1
    for folder, labels in sorted(counts.items()):
        log.info("%s: %d items", folder, sum(labels.values()))
        for label, count in sorted(labels.items(), key=lambda pair: -pair[1]):
            log.info("    %5d  %s", count, label)
    missing = sum(
        1 for item in payload["items"] if item["kind"] == "video" and not item["duration_s"]
    )
    if missing:
        log.warning("%d videos have no probed duration (single frame at t=0)", missing)
    return payload
