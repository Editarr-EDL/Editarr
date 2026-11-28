"""Per-model `<id>.meta.json`: backend metadata plus run statistics and throughput.

Memory fields (MiB, from the last run that scored frames):
- `server_peak_rss_mb`: peak resident memory of the backend's model server process
  (llama.cpp backends), sampled while scoring; absent for in-process backends.
- `process_peak_rss_mb`: the harness Python process's high-water mark when the run
  ended. It covers the whole process lifetime, so under `all` it also includes the
  models scored earlier in the same process.
"""

from __future__ import annotations

import json
import platform
from typing import Any

from .backends import Backend
from .paths import Layout, write_json
from .run_config import fingerprint


def process_peak_rss_mb() -> float:
    """Process-lifetime high-water resident set in MiB (ru_maxrss is bytes on macOS, KiB
    on Linux); 0.0 where `resource` is unavailable (Windows)."""
    try:
        import resource
    except ImportError:
        return 0.0
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round((peak if platform.system() == "Darwin" else peak * 1024) / 2**20, 1)


def server_peak_rss_mb(backend: Backend) -> float | None:
    """Peak RSS in MiB of the backend's model server (`peak_rss_bytes`), or None."""
    peak = getattr(backend, "peak_rss_bytes", None)
    return round(peak / 2**20, 1) if peak else None


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


def write_meta(
    layout: Layout, backend: Backend, stats: dict[str, Any], config: dict[str, Any] | None = None
) -> None:
    """Write meta.json: backend meta, last-run stats, throughput and, when given, the
    scoring configuration and its fingerprint (as recorded on every result line)."""
    path = layout.model_meta(backend.model_id)
    meta = backend.meta()
    if config is not None:
        meta["config"] = config
        meta["config_fingerprint"] = fingerprint(config)
    previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    meta["process_peak_rss_mb"] = stats["process_peak_rss_mb"]
    memory = f"harness process peak RSS {stats['process_peak_rss_mb']} MiB (process lifetime)"
    if stats.get("server_peak_rss_mb") is not None:
        meta["server_peak_rss_mb"] = stats["server_peak_rss_mb"]
        memory = f"model server peak RSS {stats['server_peak_rss_mb']} MiB, {memory}"
    meta["last_run"] = stats
    meta["throughput"] = accumulate_throughput(previous, stats)
    notes = meta.get("notes") or ""
    meta["notes"] = (
        f"{notes} Last run: {stats['items']} items, {stats['frames']} frames, "
        f"{stats['ms_per_frame']} ms/frame, {memory}."
    ).strip()
    write_json(path, meta)
