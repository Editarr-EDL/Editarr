"""Fixed evaluation subsets (e.g. the decision-model sample) and subset-restricted views."""

from __future__ import annotations

import json
import logging
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .inventory import top_folder
from .paths import Layout, write_json
from .results import aggregate

log = logging.getLogger(__name__)

DECISION_SAMPLE = "decision_sample"
DEFAULT_SEED = 1337
FILM_WINDOW_STRIDE = 3
PER_FOLDER_CAP = 60


def subset_path(layout: Layout, name: str):
    return layout.results / "subsets" / f"{name}.json"


def load_subset(layout: Layout, name: str) -> dict[str, list[int]]:
    """Return `{item id: [frame n, ...]}` for a subset written by `sample`."""
    path = subset_path(layout, name)
    if not path.is_file():
        raise FileNotFoundError(f"Subset {name!r} not found; run `sample` first ({path})")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {entry["id"]: list(entry["frames"]) for entry in payload["items"]}


def middle_frame(item: dict[str, Any]) -> int:
    """Frame nearest 50 % of the duration (ties go to n = len // 2); images use n = 0."""
    frames = item["frames"]
    if item.get("kind") == "image" or len(frames) == 1:
        return frames[0]["n"]
    middle = len(frames) // 2
    if not item.get("duration_s"):
        return frames[middle]["n"]
    half = item["duration_s"] / 2
    best = min(frames, key=lambda f: (abs(f["t"] - half), abs(f["n"] - middle)))
    return best["n"]


def _round_robin(groups: list[list[dict[str, Any]]], cap: int) -> list[dict[str, Any]]:
    """Take one item from each group in turn until `cap` items or all groups are exhausted."""
    picked: list[dict[str, Any]] = []
    depth = 0
    while len(picked) < cap and any(depth < len(group) for group in groups):
        for group in groups:
            if depth < len(group) and len(picked) < cap:
                picked.append(group[depth])
        depth += 1
    return picked


def build_decision_sample(
    items: list[dict[str, Any]],
    *,
    seed: int = DEFAULT_SEED,
    cap: int = PER_FOLDER_CAP,
    stride: int = FILM_WINDOW_STRIDE,
    frame_exists=lambda item, n: True,
) -> list[dict[str, Any]]:
    """Every `stride`-th scored film window (frame 0) plus a stratified clip sample.

    Clips: scored items grouped by leaf folder and expected-label set, at most `cap`
    per folder spread evenly across label sets, one middle frame each.
    """
    selected: list[dict[str, Any]] = []
    windows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    clips: dict[str, dict[tuple[str, ...], list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in items:
        if "window" in item:
            windows[item["folder"]].append(item)
        elif item.get("scored"):
            clips[item["folder"]][tuple(item["expected"])].append(item)

    for folder in sorted(windows):
        ordered = sorted(windows[folder], key=lambda item: item["window"][0])
        for item in ordered[::stride]:
            if item.get("scored") and frame_exists(item, 0):
                selected.append({"id": item["id"], "frames": [0]})

    for folder in sorted(clips):
        groups = []
        for labels in sorted(clips[folder]):
            group = sorted(clips[folder][labels], key=lambda item: item["id"])
            group = [item for item in group if frame_exists(item, middle_frame(item))]
            random.Random(f"{seed}:{folder}:{','.join(labels)}").shuffle(group)
            groups.append(group)
        for item in _round_robin(groups, cap):
            selected.append({"id": item["id"], "frames": [middle_frame(item)]})
    return selected


def write_decision_sample(layout: Layout, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    inventory = layout.load_inventory()
    files = {
        (item["id"], frame["n"]): frame["file"]
        for item in inventory["items"] for frame in item["frames"]
    }

    def frame_exists(item: dict[str, Any], n: int) -> bool:
        file = files.get((item["id"], n))
        return file is not None and (layout.results / file).is_file()

    # Sampled from inventory labels, not corrections, so the fixed sample does not drift
    # as reviewers edit ground truth; subset metrics still use corrected labels.
    entries = build_decision_sample(inventory["items"], seed=seed, frame_exists=frame_exists)
    payload = {
        "name": DECISION_SAMPLE,
        "seed": seed,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": entries,
    }
    write_json(subset_path(layout, DECISION_SAMPLE), payload)
    paths = {item["id"]: item["path"] for item in inventory["items"]}
    counts = Counter(top_folder(paths[entry["id"]]) for entry in entries)
    for folder, count in sorted(counts.items()):
        log.info("%s: %d items", folder, count)
    log.info(
        "decision_sample: %d items, %d frames",
        len(entries), sum(len(entry["frames"]) for entry in entries),
    )
    return payload


def restrict_results(
    results: dict[str, dict[str, Any]],
    subset: dict[str, list[int]],
    thresholds: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Results re-aggregated over only the subset frames; items missing any frame are dropped.

    Keys a model did not score for a frame (e.g. unasked decision-model tokens) count as 0.
    """
    restricted = {}
    for identifier, wanted in subset.items():
        record = results.get(identifier)
        if not record or record.get("status") != "ok":
            continue
        by_n = {frame["n"]: frame for frame in record.get("frames", [])}
        if not all(n in by_n for n in wanted):
            continue
        frames = [by_n[n] for n in wanted]
        scores, flagged = aggregate(frames, thresholds)
        restricted[identifier] = {
            **record, "frames": frames, "scores": scores, "flagged": flagged,
        }
    return restricted


def mean_seconds_per_frame(results: dict[str, dict[str, Any]], ids) -> float | None:
    """Mean per-item latency per frame (s): each line's elapsed / frames it scored.

    Overstates cost under --concurrency; summaries prefer the meta wall-clock throughput.
    """
    values = []
    for identifier in ids:
        record = results.get(identifier)
        if not record or record.get("status") != "ok":
            continue
        count = len(record.get("scored_n") or record.get("frames") or [])
        if count:
            values.append(record.get("elapsed_ms", 0.0) / count / 1000)
    return round(sum(values) / len(values), 4) if values else None


FILMS_QWEN300 = "films_qwen300"
FILM_SUBSET_KEYS = ("drugs", "nudity", "sex", "violence", "fear/hemophobia")


def positive_keys(expected: list[str], keys=FILM_SUBSET_KEYS) -> set[str]:
    """Keys (families or `family/token`) that `expected` labels positive."""
    return {
        key for key in keys
        if (key in expected if "/" in key else any(e.split("/")[0] == key for e in expected))
    }


def build_film_subset(
    items: list[dict[str, Any]],
    base: dict[str, list[int]],
    *,
    size: int,
    seed: int,
    min_per_key: int,
    keys=FILM_SUBSET_KEYS,
) -> list[dict[str, Any]]:
    """Deterministic stratified sample of the `base` subset's scored film windows.

    Rarest keys first, positives are added until each key has `min_per_key` (or all
    available); the rest is filled from a seeded shuffle of the remaining windows, so
    clean and positive windows keep roughly their natural proportions.
    """
    windows = sorted(
        (item for item in items
         if item["id"] in base and "window" in item and item.get("scored")),
        key=lambda item: item["id"],
    )
    random.Random(seed).shuffle(windows)
    positives = {item["id"]: positive_keys(item["expected"], keys) for item in windows}
    available = Counter(key for found in positives.values() for key in found)
    chosen: list[dict[str, Any]] = []
    taken: set[str] = set()
    counts: Counter = Counter()
    for key in sorted(keys, key=lambda key: (available[key], key)):
        for item in windows:
            if len(chosen) >= size or counts[key] >= min_per_key:
                break
            if item["id"] not in taken and key in positives[item["id"]]:
                chosen.append(item)
                taken.add(item["id"])
                counts.update(positives[item["id"]])
    for item in windows:
        if len(chosen) >= size:
            break
        if item["id"] not in taken:
            chosen.append(item)
            taken.add(item["id"])
    return [{"id": item["id"], "frames": list(base[item["id"]])} for item in chosen]


def write_film_subset(
    layout: Layout,
    name: str = FILMS_QWEN300,
    *,
    size: int = 300,
    seed: int = 2026,
    min_per_key: int = 30,
) -> dict[str, Any]:
    """Write `subsets/<name>.json` from decision_sample's film windows (corrected labels)."""
    from .corrections import apply_corrections, load_corrections

    items = apply_corrections(layout.load_inventory()["items"], load_corrections(layout))
    entries = build_film_subset(
        items, load_subset(layout, DECISION_SAMPLE), size=size, seed=seed,
        min_per_key=min_per_key,
    )
    payload = {
        "name": name,
        "seed": seed,
        "base": DECISION_SAMPLE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": entries,
    }
    write_json(subset_path(layout, name), payload)
    expected = {item["id"]: item["expected"] for item in items}
    counts = Counter(
        key for entry in entries for key in positive_keys(expected[entry["id"]])
    )
    clean = sum(1 for entry in entries if not positive_keys(expected[entry["id"]]))
    log.info("%s: %d windows, positives %s, clean %d", name, len(entries), dict(counts), clean)
    return payload


def subset_names(layout: Layout) -> list[str]:
    """Names of every subset file in `_results/subsets/`."""
    folder = layout.results / "subsets"
    return sorted(path.stem for path in folder.glob("*.json")) if folder.is_dir() else []
