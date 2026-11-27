"""Clip-level precision/recall per model, family, and ground-truth token."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .corrections import (
    apply_corrections,
    correction_count,
    corrections_stamp,
    load_corrections,
)
from .inventory import top_folder
from .labels import family_of
from .paths import Layout, write_json
from .results import load_results, write_model_index
from .run_meta import wall_ms_per_frame
from .subsets import (
    DECISION_SAMPLE,
    load_subset,
    mean_seconds_per_frame,
    restrict_results,
    subset_names,
)


def metric_row(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    """Confusion counts and P/R/F1 from (expected, predicted) pairs."""
    tp = sum(1 for expected, predicted in pairs if expected and predicted)
    fp = sum(1 for expected, predicted in pairs if not expected and predicted)
    fn = sum(1 for expected, predicted in pairs if expected and not predicted)
    tn = len(pairs) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
        "f1": None if f1 is None else round(f1, 4),
        "n": len(pairs),
    }


def _expected_positive(key: str, expected: list[str]) -> bool:
    if "/" in key:
        return key in expected
    return any(family_of(candidate) == key for candidate in expected)


def _rows(keys: list[str], pairs: list[tuple[list[str], list[str]]]) -> dict[str, Any]:
    return {
        key: metric_row([(_expected_positive(key, exp), key in flagged) for exp, flagged in pairs])
        for key in keys
    }


def _group(
    ok: list[tuple[dict[str, Any], dict[str, Any]]], supports: set[str], families: list[str]
) -> dict[str, Any]:
    """Family rows plus token rows for tokens that have ground truth in this group."""
    truth_tokens = {key for item, _ in ok for key in item["expected"] if "/" in key}
    pairs = [(item["expected"], record.get("flagged", [])) for item, record in ok]
    return {
        "families": _rows(families, pairs),
        "tokens": _rows(sorted(truth_tokens & supports), pairs),
    }


def summarize_model(
    items: list[dict[str, Any]], results: dict[str, dict[str, Any]], meta: dict[str, Any]
) -> dict[str, Any]:
    """Summarize one model over scored items that have an ok result.

    Top-level rows cover clip datasets; film windows are summarized under `films`
    (overall and per film) so they do not dilute clip-level metrics.
    """
    supports = set(meta.get("supports", []))
    # Family rows only for families a model scores directly; a token-only model (e.g.
    # siglip2-gore → fear/hemophobia) gets token rows, not a misleading family row.
    families = sorted(key for key in supports if "/" not in key)
    scored = [item for item in items if item.get("scored")]
    ok = [
        (item, results[item["id"]]) for item in scored
        if results.get(item["id"], {}).get("status") == "ok"
    ]
    clips = [pair for pair in ok if "window" not in pair[0]]
    clip_items = [item for item in scored if "window" not in item]
    frames = sum(len(record.get("frames", [])) for _item, record in ok)
    elapsed = sum(record.get("elapsed_ms", 0.0) for _item, record in ok)
    # A line's elapsed_ms covers only the frames it scored (`scored_n`), not reused ones.
    timed_frames = sum(
        len(record.get("scored_n") or record.get("frames", [])) for _item, record in ok
    )
    # Per-item latency overlaps other items under --concurrency; prefer the runner's
    # wall-clock throughput from meta when it is known.
    latency = round(elapsed / timed_frames, 2) if timed_frames else None
    throughput = wall_ms_per_frame(meta)
    folder_tokens = sorted(
        {key for item, _ in ok for key in item["expected"] if "/" in key} & supports
    )
    by_folder: dict[str, list] = {}
    films: dict[str, list] = {}
    for pair in ok:
        by_folder.setdefault(top_folder(pair[0]["path"]), []).append(pair)
        if "window" in pair[0]:
            films.setdefault(pair[0]["folder"], []).append(pair)
    film_section: dict[str, Any] = {}
    if films:
        all_windows = [pair for group in films.values() for pair in group]
        film_section["all"] = {
            "windows": len(all_windows), **_group(all_windows, supports, families)
        }
        for folder, group in sorted(films.items()):
            film_section[folder] = {
                "imdb_id": group[0][0].get("imdb_id"),
                "windows": len(group),
                **_group(group, supports, families),
            }
    return {
        "display_name": meta.get("display_name"),
        "supports": sorted(supports),
        "thresholds": meta.get("thresholds", {}),
        "items_scored": len(clips),
        "items_error": sum(
            1 for item in clip_items if results.get(item["id"], {}).get("status") == "error"
        ),
        "items_missing": sum(1 for item in clip_items if item["id"] not in results),
        "frames": frames,
        "mean_ms_per_frame": throughput if throughput is not None else latency,
        "latency_ms_per_frame": latency,
        "timing_source": "wall_clock" if throughput is not None else "per_item_latency",
        # Metas written before the split recorded only the process peak as `peak_rss_mb`.
        "process_peak_rss_mb": meta.get("process_peak_rss_mb", meta.get("peak_rss_mb")),
        "server_peak_rss_mb": meta.get("server_peak_rss_mb"),
        **_group(clips, supports, families),
        "by_folder": {
            folder: _rows(
                families + folder_tokens, [(i["expected"], r.get("flagged", [])) for i, r in group]
            )
            for folder, group in sorted(by_folder.items())
        },
        "films": film_section,
    }


def summarize_subset(
    items: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    meta: dict[str, Any],
    subset: dict[str, list[int]],
) -> dict[str, Any]:
    """Metrics with item scores taken as the max over only the subset frames."""
    restricted = restrict_results(results, subset, meta.get("thresholds", {}))
    section = summarize_model(
        [item for item in items if item["id"] in subset], restricted, meta
    )
    section.pop("mean_ms_per_frame", None)
    section.pop("latency_ms_per_frame", None)
    section["items_total"] = len(subset)
    section["items_with_results"] = len(restricted)
    section["frames"] = sum(len(record["frames"]) for record in restricted.values())
    latency = mean_seconds_per_frame(results, subset)
    throughput = wall_ms_per_frame(meta)
    section["mean_s_per_frame"] = round(throughput / 1000, 4) if throughput is not None else latency
    section["latency_s_per_frame"] = latency
    return section


MAX_SUMMARY_ATTEMPTS = 3


def build_summary(layout: Layout, corrections: dict[str, Any]) -> dict[str, Any]:
    """Summary payload for the given corrections (`corrections.updated_at` = those used)."""
    inventory = layout.load_inventory()
    items = apply_corrections(inventory["items"], corrections)
    subsets = {name: load_subset(layout, name) for name in subset_names(layout)}
    models: dict[str, Any] = {}
    for model_id in write_model_index(layout):
        meta_path = layout.model_meta(model_id)
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        results = load_results(layout.model_jsonl(model_id))
        models[model_id] = summarize_model(items, results, meta)
        # Every model keeps a decision_sample section (the viewer expects it); other
        # subsets appear only for models with results on their items.
        sections = {}
        for name, subset in subsets.items():
            section = summarize_subset(items, results, meta, subset)
            if name == DECISION_SAMPLE or section["items_with_results"]:
                sections[name] = section
        models[model_id]["subsets"] = sections
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "inventory_generated_at": inventory.get("generated_at"),
        "corrections": {
            "count": correction_count(corrections),
            "updated_at": corrections.get("updated_at"),
        },
        "subsets": {
            name: {"subset_ids": sorted(subset), "frames": subset}
            for name, subset in subsets.items()
        },
        "models": models,
    }


def summarize(layout: Layout) -> dict[str, Any]:
    """Write `summary.json` for every model with results, plus `models/index.json`.

    If corrections.json changes while summarizing, the summary is recomputed (up to
    MAX_SUMMARY_ATTEMPTS) so a slow run never overwrites a newer one with stale labels.
    """
    for _attempt in range(MAX_SUMMARY_ATTEMPTS):
        stamp = corrections_stamp(layout)
        payload = build_summary(layout, load_corrections(layout))
        if corrections_stamp(layout) == stamp:
            break
    write_json(layout.summary, payload)
    return payload
