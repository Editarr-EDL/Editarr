"""Evaluate the deterministic credits detector against the authored MTS corpus."""

from __future__ import annotations

import json
import math
import platform
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import cv2

from .credits_detector import (
    DETECTOR_VERSION,
    ChapterMarker,
    FrameObservation,
    default_configuration,
    detect_from_observations,
)

CORPUS_VERSION = "mts-credits-evaluation-v1"
PYSCENEDETECT_ADAPTER_VERSION = "mts-credits-pyscenedetect-v1"
PYSCENEDETECT_REVISION = "6ebb72392de8acfb6c539bf15d0aa912ce7ab6b2"
PYSCENEDETECT_WHEEL_SHA256 = (
    "91b67902275be0d29a12f6d70435f04e0bd7852a6dc8a69c901c6069328dffa"
)
PYSCENEDETECT_WHEEL_SIZE_BYTES = 146_194
PYSCENEDETECT_THRESHOLD = 27.0
REQUIRED_TAGS = frozenset(
    {
        "scrolling",
        "static_cards",
        "moving_imagery",
        "post_credits",
        "credible_chapter",
        "missing_chapters",
        "misleading_chapter",
        "no_credits",
        "non_english",
        "black_frames",
        "ambiguous",
    }
)


@dataclass(frozen=True)
class CreditsEvaluationCase:
    case_id: str
    source: str
    duration_seconds: float
    expected_boundary_seconds: float | None
    tolerance_seconds: float
    tags: tuple[str, ...]
    chapters: tuple[ChapterMarker, ...]
    observations: tuple[FrameObservation, ...]
    scene_boundaries: tuple[float, ...]


@dataclass(frozen=True)
class CreditsEvaluationCorpus:
    version: str
    license: str
    source: str
    cases: tuple[CreditsEvaluationCase, ...]


def detect_pyscenedetect_boundaries(
    frames: Sequence[tuple[Path, float]],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> list[float]:
    """Detect cuts between bounded sampled frames with the installed PySceneDetect."""
    from scenedetect import ContentDetector, FrameTimecode

    from scanner.pipeline.processes import ScanCancelledError

    ordered_frames = sorted(frames, key=lambda item: item[1])
    detector = ContentDetector(threshold=PYSCENEDETECT_THRESHOLD, min_scene_len=1)
    boundaries: list[float] = []
    for index, (frame_path, _timestamp) in enumerate(ordered_frames):
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        image = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise ValueError(f"Could not read sampled frame: {frame_path.name}")
        for cut in detector.process_frame(FrameTimecode(index, fps=1.0), image):
            boundaries.append(float(ordered_frames[cut.frame_num][1]))
    return sorted(set(boundaries))


def _number(value: object, *, field: str, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"Credits corpus {field} must be finite and at least {minimum}")
    return float(value)


def _load_case(payload: object, index: int) -> CreditsEvaluationCase:
    """Load and validate one credits evaluation case from its fixture directory."""
    fields = {
        "id",
        "source",
        "duration_seconds",
        "expected_boundary_seconds",
        "tolerance_seconds",
        "tags",
        "chapters",
        "observations",
        "scene_boundaries",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError(f"Credits corpus case {index} has an invalid schema")
    case_id = payload["id"]
    source = payload["source"]
    tags = payload["tags"]
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"Credits corpus case {index} needs an id")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"Credits corpus case {index} needs a source")
    if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) for tag in tags):
        raise ValueError(f"Credits corpus case {index} needs tags")
    duration = _number(payload["duration_seconds"], field="duration", minimum=0.001)
    tolerance = _number(payload["tolerance_seconds"], field="tolerance")
    expected_raw = payload["expected_boundary_seconds"]
    expected = (
        None
        if expected_raw is None
        else _number(expected_raw, field="expected boundary")
    )
    if expected is not None and expected > duration:
        raise ValueError(f"Credits corpus case {index} boundary exceeds duration")
    try:
        chapters = tuple(ChapterMarker(**item) for item in payload["chapters"])
        observations = tuple(FrameObservation(**item) for item in payload["observations"])
        scenes = tuple(float(value) for value in payload["scene_boundaries"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Credits corpus case {index} has invalid evidence") from exc
    timestamps = (
        [item.start_seconds for item in chapters]
        + [item.timestamp_seconds for item in observations]
        + list(scenes)
    )
    if any(not math.isfinite(value) or not 0 <= value <= duration for value in timestamps):
        raise ValueError(f"Credits corpus case {index} evidence exceeds duration")
    return CreditsEvaluationCase(
        case_id=case_id.strip(),
        source=source.strip(),
        duration_seconds=duration,
        expected_boundary_seconds=expected,
        tolerance_seconds=tolerance,
        tags=tuple(tags),
        chapters=chapters,
        observations=observations,
        scene_boundaries=scenes,
    )


def load_credits_evaluation_corpus(path: Path) -> CreditsEvaluationCorpus:
    """Load and validate the bounded legal credits corpus."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read credits corpus: {path}") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "license",
        "source",
        "cases",
    }:
        raise ValueError("Credits corpus has an invalid top-level schema")
    if payload["version"] != CORPUS_VERSION:
        raise ValueError("Unsupported credits corpus version")
    if payload["license"] != "CC0-1.0" or not isinstance(payload["source"], str):
        raise ValueError("Credits corpus must declare its legal source")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases or len(raw_cases) > 1_000:
        raise ValueError("Credits corpus must contain 1 to 1000 cases")
    cases = tuple(_load_case(item, index) for index, item in enumerate(raw_cases, start=1))
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Credits corpus case ids must be unique")
    represented_tags = {tag for case in cases for tag in case.tags}
    if missing := REQUIRED_TAGS - represented_tags:
        raise ValueError(f"Credits corpus is missing required tags: {', '.join(sorted(missing))}")
    return CreditsEvaluationCorpus(
        version=payload["version"],
        license=payload["license"],
        source=payload["source"],
        cases=cases,
    )


def evaluate_credits_corpus(path: Path) -> dict[str, Any]:
    """Measure boundary behavior on the synthetic corpus without a population claim."""
    corpus = load_credits_evaluation_corpus(path)
    started = time.perf_counter()
    case_results: list[dict[str, Any]] = []
    positive_count = 0
    recalled_count = 0
    null_count = 0
    false_positive_count = 0
    errors: list[float] = []
    band_totals = {"low": 0, "medium": 0, "high": 0}
    band_within_tolerance = {"low": 0, "medium": 0, "high": 0}
    for case in corpus.cases:
        result = detect_from_observations(
            duration_seconds=case.duration_seconds,
            configuration=default_configuration(),
            chapters=case.chapters,
            scene_boundaries=case.scene_boundaries,
            observations=case.observations,
        )
        candidate = result.candidates[0] if result.candidates else None
        error = None
        within_tolerance = False
        if case.expected_boundary_seconds is None:
            null_count += 1
            false_positive_count += candidate is not None
        else:
            positive_count += 1
            if candidate is not None:
                error = abs(candidate.boundary_seconds - case.expected_boundary_seconds)
                errors.append(error)
                within_tolerance = error <= case.tolerance_seconds
                recalled_count += within_tolerance
                band = candidate.confidence_band.value
                band_totals[band] += 1
                band_within_tolerance[band] += within_tolerance
        case_results.append(
            {
                "id": case.case_id,
                "expected_boundary_seconds": case.expected_boundary_seconds,
                "predicted_boundary_seconds": (
                    candidate.boundary_seconds if candidate is not None else None
                ),
                "absolute_error_seconds": error,
                "within_tolerance": within_tolerance,
                "confidence": candidate.confidence if candidate is not None else None,
                "confidence_band": (
                    candidate.confidence_band.value if candidate is not None else None
                ),
                "outcome": result.outcome,
            }
        )
    policy_runtime_seconds = time.perf_counter() - started
    calibration = {
        band: {
            "candidate_count": band_totals[band],
            "within_tolerance_count": band_within_tolerance[band],
            "within_tolerance_rate": (
                band_within_tolerance[band] / band_totals[band]
                if band_totals[band]
                else None
            ),
        }
        for band in band_totals
    }
    rendered_results = []
    rendered_recalled = 0
    rendered_false_positives = 0
    rendered_errors: list[float] = []
    rendered_analysis_runtime_seconds = 0.0
    rendered_peak_python_memory_bytes = 0
    pyscenedetect_results = []
    pyscenedetect_recalled = 0
    pyscenedetect_false_positives = 0
    pyscenedetect_errors: list[float] = []
    pyscenedetect_band_counts = {"low": 0, "medium": 0, "high": 0}
    pyscenedetect_runtime_seconds = 0.0
    pyscenedetect_peak_python_memory_bytes = 0
    from .credits_fixture_generator import generate_credits_fixture_corpus
    from .credits_media import analyze_sampled_frames

    rendered_started = time.perf_counter()
    with tempfile.TemporaryDirectory() as directory:
        manifests = generate_credits_fixture_corpus(path, Path(directory))
        for case, manifest_path in zip(corpus.cases, manifests, strict=True):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            frames = [
                (manifest_path.parent / item["path"], item["timestamp_seconds"])
                for item in manifest["frames"]
            ]
            tracemalloc.start()
            analysis_started = time.perf_counter()
            observations, _inferred_scenes = analyze_sampled_frames(frames)
            result = detect_from_observations(
                duration_seconds=case.duration_seconds,
                configuration=default_configuration(),
                chapters=case.chapters,
                scene_boundaries=case.scene_boundaries,
                observations=observations,
            )
            rendered_analysis_runtime_seconds += time.perf_counter() - analysis_started
            _current_memory, peak_memory = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            rendered_peak_python_memory_bytes = max(
                rendered_peak_python_memory_bytes, peak_memory
            )
            candidate = result.candidates[0] if result.candidates else None
            if case.expected_boundary_seconds is None:
                within_tolerance = candidate is None
                rendered_false_positives += candidate is not None
                rendered_error = None
            else:
                rendered_error = (
                    abs(candidate.boundary_seconds - case.expected_boundary_seconds)
                    if candidate is not None
                    else None
                )
                if rendered_error is not None:
                    rendered_errors.append(rendered_error)
                within_tolerance = (
                    rendered_error is not None
                    and rendered_error <= case.tolerance_seconds
                )
                rendered_recalled += within_tolerance
            rendered_results.append(
                {
                    "id": case.case_id,
                    "predicted_boundary_seconds": (
                        candidate.boundary_seconds if candidate is not None else None
                    ),
                    "absolute_error_seconds": rendered_error,
                    "within_tolerance": within_tolerance,
                    "confidence_band": (
                        candidate.confidence_band.value if candidate is not None else None
                    ),
                }
            )
            tracemalloc.start()
            candidate_started = time.perf_counter()
            candidate_observations, _candidate_inferred_scenes = analyze_sampled_frames(
                frames
            )
            candidate_scenes = detect_pyscenedetect_boundaries(frames)
            candidate_result = detect_from_observations(
                duration_seconds=case.duration_seconds,
                configuration=default_configuration(),
                chapters=case.chapters,
                scene_boundaries=candidate_scenes,
                observations=candidate_observations,
            )
            pyscenedetect_runtime_seconds += time.perf_counter() - candidate_started
            _current_memory, peak_memory = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            pyscenedetect_peak_python_memory_bytes = max(
                pyscenedetect_peak_python_memory_bytes, peak_memory
            )
            pyscenedetect_candidate = (
                candidate_result.candidates[0] if candidate_result.candidates else None
            )
            if case.expected_boundary_seconds is None:
                candidate_within_tolerance = pyscenedetect_candidate is None
                pyscenedetect_false_positives += pyscenedetect_candidate is not None
                candidate_error = None
            else:
                candidate_error = (
                    abs(
                        pyscenedetect_candidate.boundary_seconds
                        - case.expected_boundary_seconds
                    )
                    if pyscenedetect_candidate is not None
                    else None
                )
                candidate_within_tolerance = (
                    candidate_error is not None
                    and candidate_error <= case.tolerance_seconds
                )
                pyscenedetect_recalled += candidate_within_tolerance
                if candidate_error is not None:
                    pyscenedetect_errors.append(candidate_error)
            if pyscenedetect_candidate is not None:
                pyscenedetect_band_counts[pyscenedetect_candidate.confidence_band.value] += 1
            pyscenedetect_results.append(
                {
                    "id": case.case_id,
                    "predicted_boundary_seconds": (
                        pyscenedetect_candidate.boundary_seconds
                        if pyscenedetect_candidate is not None
                        else None
                    ),
                    "absolute_error_seconds": candidate_error,
                    "within_tolerance": candidate_within_tolerance,
                    "scene_boundaries": candidate_scenes,
                    "confidence_band": (
                        pyscenedetect_candidate.confidence_band.value
                        if pyscenedetect_candidate is not None
                        else None
                    ),
                }
            )
    rendered_runtime_seconds = time.perf_counter() - rendered_started
    return {
        "corpus": {
            "version": corpus.version,
            "license": corpus.license,
            "source": corpus.source,
            "case_count": len(corpus.cases),
        },
        "detector_version": DETECTOR_VERSION,
        "backend": "deterministic_synthetic_observations",
        "platform": platform.platform(),
        "runtime_seconds": time.perf_counter() - started,
        "policy_runtime_seconds": policy_runtime_seconds,
        "candidate_recall": {
            "within_tolerance": recalled_count,
            "positive_cases": positive_count,
            "rate": recalled_count / positive_count if positive_count else None,
        },
        "false_positives": {
            "count": false_positive_count,
            "null_boundary_cases": null_count,
            "rate": false_positive_count / null_count if null_count else None,
        },
        "absolute_boundary_error_seconds": {
            "mean": sum(errors) / len(errors) if errors else None,
            "maximum": max(errors) if errors else None,
        },
        "confidence_calibration": calibration,
        "rendered_tail_validation": {
            "generator_version": "mts-credits-generated-tail-v1",
            "backend": "opencv_generated_png_sequence",
            "runtime_seconds": rendered_runtime_seconds,
            "analysis_runtime_seconds": rendered_analysis_runtime_seconds,
            "peak_python_memory_bytes": rendered_peak_python_memory_bytes,
            "candidate_recall": {
                "within_tolerance": rendered_recalled,
                "positive_cases": positive_count,
                "rate": rendered_recalled / positive_count if positive_count else None,
            },
            "false_positives": {
                "count": rendered_false_positives,
                "null_boundary_cases": null_count,
                "rate": rendered_false_positives / null_count if null_count else None,
            },
            "absolute_boundary_error_seconds": {
                "mean": (
                    sum(rendered_errors) / len(rendered_errors)
                    if rendered_errors
                    else None
                ),
                "maximum": max(rendered_errors) if rendered_errors else None,
            },
            "cases": rendered_results,
        },
        "third_party_comparison": {
            "pyscenedetect": {
                "adapter_version": PYSCENEDETECT_ADAPTER_VERSION,
                "package_version": (pyscenedetect_version := version("scenedetect")),
                "upstream_revision": (
                    PYSCENEDETECT_REVISION
                    if pyscenedetect_version == "0.7.1"
                    else None
                ),
                "package_artifact_size_bytes": PYSCENEDETECT_WHEEL_SIZE_BYTES,
                "package_sha256": PYSCENEDETECT_WHEEL_SHA256,
                "threshold": PYSCENEDETECT_THRESHOLD,
                "model_artifact_size_bytes": 0,
                "backend": "pyscenedetect_content_detector_sampled_frames",
                "runtime_seconds": pyscenedetect_runtime_seconds,
                "peak_python_memory_bytes": pyscenedetect_peak_python_memory_bytes,
                "candidate_recall": {
                    "within_tolerance": pyscenedetect_recalled,
                    "positive_cases": positive_count,
                    "rate": (
                        pyscenedetect_recalled / positive_count if positive_count else None
                    ),
                },
                "false_positives": {
                    "count": pyscenedetect_false_positives,
                    "null_boundary_cases": null_count,
                    "rate": (
                        pyscenedetect_false_positives / null_count if null_count else None
                    ),
                },
                "absolute_boundary_error_seconds": {
                    "mean": (
                        sum(pyscenedetect_errors) / len(pyscenedetect_errors)
                        if pyscenedetect_errors
                        else None
                    ),
                    "maximum": max(pyscenedetect_errors) if pyscenedetect_errors else None,
                },
                "confidence_band_counts": pyscenedetect_band_counts,
                "cases": pyscenedetect_results,
            }
        },
        "limitations": [
            "Synthetic cases verify deterministic policy behavior, not population accuracy.",
            "Confidence bands are presentation labels and are not probability estimates.",
            "Peak memory includes Python-managed allocations only, not native OpenCV memory.",
            "Real-media and optional Qwen quality require separate reviewed evidence.",
        ],
        "cases": case_results,
    }
