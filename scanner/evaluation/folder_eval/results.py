"""Model result lines: thresholds, per-item aggregation, merging, reading and appending."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .labels import family_of
from .paths import Layout, write_json

DEFAULT_THRESHOLD = 0.5


def threshold_for(key: str, thresholds: dict[str, float]) -> float:
    for candidate in (key, family_of(key), "*"):
        if candidate in thresholds:
            return float(thresholds[candidate])
    return DEFAULT_THRESHOLD


def aggregate(frames: list[dict[str, Any]], thresholds: dict[str, float]) -> tuple[dict, list]:
    """Return item scores (max over frames) and flagged keys with implied families."""
    scores: dict[str, float] = {}
    for frame in frames:
        for key, value in frame.get("scores", {}).items():
            scores[key] = max(scores.get(key, 0.0), float(value))
    flagged = {key for key, value in scores.items() if value >= threshold_for(key, thresholds)}
    flagged |= {family_of(key) for key in flagged}
    return scores, sorted(flagged)


def merge_record(
    previous: dict[str, Any] | None,
    record: dict[str, Any],
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Combine two lines for one item: frames merge by `n` (newer wins) and errors never
    replace an earlier ok result. Item scores and flags are recomputed from the merged
    frames with `thresholds`, so a replaced frame's old flag does not survive."""
    if previous is None:
        return record
    if record.get("status") != "ok":
        if previous.get("status") == "ok":
            return {**previous, "last_error": record.get("error")}
        return record
    if previous.get("status") != "ok":
        return record
    by_n = {frame["n"]: frame for frame in previous.get("frames", [])}
    if set(by_n) <= {frame["n"] for frame in record.get("frames", [])}:
        return record
    by_n.update({frame["n"]: frame for frame in record.get("frames", [])})
    frames = [by_n[n] for n in sorted(by_n)]
    scores, flagged = aggregate(frames, thresholds or {})
    return {**record, "frames": frames, "scores": scores, "flagged": flagged}


def _meta_thresholds(path: Path) -> dict[str, float]:
    """Thresholds from the `<id>.meta.json` next to a model's `<id>.jsonl`, if any."""
    meta = path.with_name(f"{path.stem}.meta.json")
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("thresholds") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_results(
    path: Path, thresholds: dict[str, float] | None = None
) -> dict[str, dict[str, Any]]:
    """Read a model jsonl, merging repeated lines per id with `merge_record`.

    `thresholds` default to those in the model's meta.json next to `path`.
    """
    results: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return results
    if thresholds is None:
        thresholds = _meta_thresholds(path)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # A run killed mid-write can leave a truncated last line.
            results[record["id"]] = merge_record(results.get(record["id"]), record, thresholds)
    return results


def append_line(descriptor: int, record: dict[str, Any]) -> None:
    """Append one JSON line with a single O_APPEND write so concurrent runs never interleave.

    The record starts with a newline too, so a fragment left by an interrupted write ends
    there instead of absorbing this record; readers skip the resulting blank lines.
    """
    data = ("\n" + json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    written = os.write(descriptor, data)
    if written != len(data):
        raise OSError(f"short append ({written}/{len(data)} bytes)")


def write_model_index(layout: Layout) -> list[str]:
    """Write `models/index.json` listing model ids that have results or metadata."""
    ids = set()
    if layout.models.is_dir():
        for path in layout.models.iterdir():
            if path.name.endswith(".meta.json"):
                ids.add(path.name.removesuffix(".meta.json"))
            elif path.suffix == ".jsonl":
                ids.add(path.stem)
    ordered = sorted(ids)
    write_json(layout.models / "index.json", ordered)
    return ordered
