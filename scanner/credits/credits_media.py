"""Bounded media adapters for the pure closing-credits detector."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from pathlib import Path

import cv2
import numpy as np

from scanner.models import CreditsDetectionConfiguration, CreditsDetectionResult
from scanner.pipeline.scenes import Scene, sample_scene_frames

from .credits_detector import (
    DETECTOR_VERSION,
    PROMPT_VERSION,
    ChapterMarker,
    FrameObservation,
    QwenAssessment,
    detect_from_observations,
)

QWEN_CREDITS_PROMPT = (
    f"Protocol {PROMPT_VERSION}. Inspect these chronological frames from a video's tail. "
    "Decide only whether closing credits are visibly present. Opening titles, subtitles, "
    "logos, isolated title cards, black frames, and a final scene are not sufficient. "
    "Return exactly one JSON object with keys credits_visible (boolean), confidence (number "
    "from 0 to 1), boundary_frame_index (zero-based integer), and evidence (short string)."
)


def read_container_chapters(video_path: str) -> list[ChapterMarker]:
    """Read finite chapter starts and titles from the selected container."""
    import av

    chapters: list[ChapterMarker] = []
    try:
        with av.open(video_path) as container:
            container_chapters = container.chapters
            if callable(container_chapters):
                container_chapters = container_chapters()
            for chapter in container_chapters:
                start = getattr(chapter, "start", None)
                time_base = getattr(chapter, "time_base", None)
                metadata = getattr(chapter, "metadata", {}) or {}
                title = metadata.get("title") if isinstance(metadata, dict) else None
                if start is None or time_base is None or not isinstance(title, str):
                    continue
                timestamp = float(start * time_base)
                if math.isfinite(timestamp) and timestamp >= 0 and title.strip():
                    chapters.append(ChapterMarker(timestamp, title.strip()))
    except (OSError, ValueError):
        return []
    return sorted(chapters, key=lambda item: (item.start_seconds, item.title))


def _load_gray(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        raise ValueError(f"Could not read sampled frame: {path.name}")
    return image


def _frame_scores(gray: np.ndarray) -> tuple[float, float]:
    mean_luma = float(np.mean(gray))
    black_frame = float(np.clip(1.0 - mean_luma / 48.0, 0.0, 1.0))
    edges = cv2.Canny(gray, 60, 160)
    edge_density = float(np.count_nonzero(edges) / edges.size)
    active_rows = float(np.mean(np.mean(edges > 0, axis=1) >= 0.015))
    text_density = float(np.clip((edge_density / 0.045) * 0.75 + active_rows * 0.4, 0.0, 1.0))
    return text_density, black_frame


def analyze_sampled_frames(
    frames: Sequence[tuple[Path, float]],
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[list[FrameObservation], list[float]]:
    """Measure text-like structure, motion, darkness, and coarse scene changes."""
    observations: list[FrameObservation] = []
    scene_boundaries: list[float] = []
    previous_gray: np.ndarray | None = None
    previous_text_density = 0.0
    for frame_path, timestamp in sorted(frames, key=lambda item: item[1]):
        if should_cancel and should_cancel():
            from scanner.pipeline.processes import ScanCancelledError

            raise ScanCancelledError("Scan cancelled")
        gray = _load_gray(frame_path)
        text_density, black_frame = _frame_scores(gray)
        credit_motion = 0.0
        if previous_gray is not None:
            resized = cv2.resize(previous_gray, (gray.shape[1], gray.shape[0]))
            difference = float(np.mean(cv2.absdiff(resized, gray)) / 255.0)
            credit_motion = float(
                np.clip(
                    min(previous_text_density, text_density) * 0.45
                    + min(difference / 0.12, 1.0) * 0.55,
                    0.0,
                    1.0,
                )
            )
            if difference >= 0.32:
                scene_boundaries.append(float(timestamp))
        observations.append(
            FrameObservation(
                timestamp_seconds=float(timestamp),
                text_density=text_density,
                credit_motion=credit_motion,
                black_frame=black_frame,
            )
        )
        previous_gray = gray
        previous_text_density = text_density
    return observations, scene_boundaries


def assess_frames_with_qwen(
    frame_paths: Sequence[Path],
    timestamps: Sequence[float],
    *,
    qwen_model: str,
) -> list[QwenAssessment]:
    """Run the existing pinned Qwen stack after the caller has established consent/readiness."""
    if not frame_paths or len(frame_paths) != len(timestamps):
        raise ValueError("Qwen credits assessment requires aligned frames and timestamps")
    from scanner.pipeline.qwen_summary import summarize_frames

    summary = summarize_frames(
        list(frame_paths), prompt=QWEN_CREDITS_PROMPT, qwen_model=qwen_model
    )
    try:
        payload = json.loads(summary.explanation)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict) or set(payload) != {
        "credits_visible",
        "confidence",
        "boundary_frame_index",
        "evidence",
    }:
        return []
    visible = payload["credits_visible"]
    confidence = payload["confidence"]
    index = payload["boundary_frame_index"]
    evidence = payload["evidence"]
    if (
        visible is not True
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
        or isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index < len(timestamps)
        or not isinstance(evidence, str)
        or not evidence.strip()
    ):
        return []
    return [QwenAssessment(float(timestamps[index]), float(confidence), evidence.strip())]


def detect_credits_in_media(
    video_path: str,
    *,
    duration_seconds: float,
    configuration: CreditsDetectionConfiguration,
    workspace: Path,
    cached_scene_boundaries: Sequence[float] | None = None,
    qwen_assessor: Callable[[Sequence[Path], Sequence[float], str], Sequence[QwenAssessment]]
    | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> CreditsDetectionResult:
    """Extract a bounded tail and run the pure detector without persistence or queue effects."""
    if configuration.detector_version != DETECTOR_VERSION:
        raise ValueError("Unsupported credits detector version")
    tail_start = max(0.0, duration_seconds - configuration.tail_window_seconds)
    tail = Scene(start_seconds=tail_start, end_seconds=duration_seconds, index=0)
    frames = sample_scene_frames(
        video_path,
        tail,
        workspace,
        configuration.frame_sample_interval_seconds,
        timestamp_interval_seconds=configuration.frame_sample_interval_seconds,
        should_cancel=should_cancel,
    )
    observations, inferred_scene_boundaries = analyze_sampled_frames(
        frames, should_cancel=should_cancel
    )
    scene_boundaries = (
        [value for value in cached_scene_boundaries if value >= tail_start]
        if cached_scene_boundaries is not None
        else inferred_scene_boundaries
    )
    qwen_assessments: Sequence[QwenAssessment] = ()
    if configuration.qwen_model is not None and qwen_assessor is not None and frames:
        qwen_assessments = qwen_assessor(
            [path for path, _timestamp in frames],
            [timestamp for _path, timestamp in frames],
            configuration.qwen_model,
        )
    return detect_from_observations(
        duration_seconds=duration_seconds,
        configuration=configuration,
        chapters=read_container_chapters(video_path),
        scene_boundaries=scene_boundaries,
        observations=observations,
        qwen_assessments=qwen_assessments,
        should_cancel=should_cancel,
    )
