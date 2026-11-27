"""Per-model `<id>.meta.json`: backend metadata plus run statistics and throughput."""

from __future__ import annotations

import json
import platform
from typing import Any

from .backends import Backend
from .paths import Layout, write_json


def peak_rss_mb() -> float:
    """Process high-water resident set in MiB (ru_maxrss is bytes on macOS, KiB on Linux);
    0.0 where `resource` is unavailable (Windows)."""
    try:
        import resource
    except ImportError:
        return 0.0
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round((peak if platform.system() == "Darwin" else peak * 1024) / 2**20, 1)


def accumulate_throughput(previous: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    """Add this run's wall time and frames to the per-model totals kept in meta.

    Seeds from an older meta's `last_run` when it recorded wall throughput.
    """
    totals = dict(previous.get("throughput") or {"wall_ms": 0.0, "frames": 0, "runs": 0})
    if not previous.get("throughput"):
        last = previous.get("last_run") or {}
        if last.get("wall_ms_per_frame") is not None and last.get("frames"):
            totals = {"wall_ms": last["elapsed_ms"], "frames": last["frames"], "runs": 1}
    if stats.get("frames") and stats.get("wall_ms_per_frame") is not None:
        totals["wall_ms"] = round(totals["wall_ms"] + stats["elapsed_ms"], 1)
        totals["frames"] += stats["frames"]
        totals["runs"] += 1
    return totals


def wall_ms_per_frame(meta: dict[str, Any]) -> float | None:
    """Wall-clock ms per frame across recorded runs (throughput), or None if unknown."""
    totals = meta.get("throughput") or {}
    if totals.get("frames"):
        return round(totals["wall_ms"] / totals["frames"], 2)
    return (meta.get("last_run") or {}).get("wall_ms_per_frame")


def write_meta(layout: Layout, backend: Backend, stats: dict[str, Any]) -> None:
    path = layout.model_meta(backend.model_id)
    meta = backend.meta()
    previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    peak = max(stats["peak_rss_mb"], previous.get("peak_rss_mb") or 0.0)
    meta["peak_rss_mb"] = peak
    meta["last_run"] = stats
    meta["throughput"] = accumulate_throughput(previous, stats)
    notes = meta.get("notes") or ""
    meta["notes"] = (
        f"{notes} Last run: {stats['items']} items, {stats['frames']} frames, "
        f"{stats['ms_per_frame']} ms/frame, peak RSS {peak} MiB."
    ).strip()
    write_json(path, meta)
