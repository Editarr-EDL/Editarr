"""Evaluate visual detector speed and accuracy against a labeled frame corpus."""

from __future__ import annotations

import json
import math
import platform
import resource
import time
from pathlib import Path
from typing import Any

from scanner.models import McfCategoryFamily
from scanner.pipeline.visual import normalize_video_model, scan_frame


def _load_dataset(dataset_path: Path) -> list[tuple[Path, tuple[McfCategoryFamily, ...]]]:
    """Load and validate image labels for video model evaluation."""
    try:
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read evaluation dataset: {dataset_path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("frames"), list):
        raise ValueError("Evaluation dataset must contain a 'frames' array")

    frames: list[tuple[Path, tuple[McfCategoryFamily, ...]]] = []
    for index, item in enumerate(payload["frames"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Evaluation dataset frame {index} must be an object")
        relative_path = item.get("path")
        expected = item.get("expected_categories")
        if not isinstance(relative_path, str) or not isinstance(expected, list):
            raise ValueError(
                f"Evaluation dataset frame {index} needs 'path' and 'expected_categories'"
            )
        frame_path = dataset_path.parent / relative_path
        if not frame_path.is_file():
            raise ValueError(f"Evaluation dataset frame is missing: {frame_path}")
        try:
            categories = tuple(McfCategoryFamily(category) for category in expected)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Evaluation dataset frame {index} has an invalid category") from exc
        frames.append((frame_path, tuple(dict.fromkeys(categories))))
    if not frames:
        raise ValueError("Evaluation dataset must contain at least one frame")
    return frames


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _metric(tp: int, fp: int, fn: int) -> dict[str, float | int | None]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _peak_rss_mb() -> float:
    """Return the process high-water resident set in MiB on supported platforms."""
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = peak_rss if platform.system() == "Darwin" else peak_rss * 1_024
    return peak_bytes / (1024 * 1024)


def evaluate_video_model(
    dataset_path: Path,
    video_model: str,
    *,
    warmup_frames: int = 1,
    runs: int = 1,
) -> dict[str, Any]:
    """Run one visual detector on a human-labeled frame dataset."""
    if warmup_frames < 0:
        raise ValueError("Warm-up frames cannot be negative")
    if runs < 1:
        raise ValueError("Runs must be at least one")
    frames = _load_dataset(dataset_path)
    model = normalize_video_model(video_model)

    warmup_started = time.perf_counter()
    for frame_path, _expected in frames[:warmup_frames]:
        scan_frame(frame_path, 0.0, video_model=model)
    warmup_seconds = time.perf_counter() - warmup_started

    latencies: list[float] = []
    frame_results = []
    for run_index in range(runs):
        for frame_path, expected in frames:
            started = time.perf_counter()
            detections = scan_frame(frame_path, 0.0, video_model=model)
            latencies.append(time.perf_counter() - started)
            if run_index != 0:
                continue
            predicted = tuple(dict.fromkeys(detection.category for detection in detections))
            frame_results.append(
                {
                    "path": str(frame_path.relative_to(dataset_path.parent)),
                    "expected_categories": [category.value for category in expected],
                    "predicted_categories": [category.value for category in predicted],
                    "detections": [
                        {
                            "label": detection.label,
                            "category": detection.category.value,
                            "confidence": detection.confidence,
                        }
                        for detection in detections
                    ],
                }
            )

    categories = sorted(
        {
            category
            for result in frame_results
            for key in ("expected_categories", "predicted_categories")
            for category in result[key]
        }
    )
    by_category = {}
    overall = [0, 0, 0]
    for category in categories:
        true_positive = sum(
            category in result["expected_categories"] and category in result["predicted_categories"]
            for result in frame_results
        )
        false_positive = sum(
            category not in result["expected_categories"]
            and category in result["predicted_categories"]
            for result in frame_results
        )
        false_negative = sum(
            category in result["expected_categories"]
            and category not in result["predicted_categories"]
            for result in frame_results
        )
        by_category[category] = _metric(true_positive, false_positive, false_negative)
        overall[0] += true_positive
        overall[1] += false_positive
        overall[2] += false_negative

    total_seconds = sum(latencies)
    return {
        "dataset": str(dataset_path),
        "video_model": model,
        "frame_count": len(frames),
        "runs": runs,
        "warmup": {"frames": min(warmup_frames, len(frames)), "seconds": warmup_seconds},
        "peak_process_rss_mb": _peak_rss_mb(),
        "timing": {
            "measured_calls": len(latencies),
            "total_seconds": total_seconds,
            "frames_per_second": len(latencies) / total_seconds if total_seconds else None,
            "latency_ms": {
                "mean": total_seconds / len(latencies) * 1_000,
                "p50": _percentile(latencies, 0.50) * 1_000,
                "p95": _percentile(latencies, 0.95) * 1_000,
                "max": max(latencies) * 1_000,
            },
        },
        "accuracy": {"overall": _metric(*overall), "by_category": by_category},
        "frame_results": frame_results,
    }
