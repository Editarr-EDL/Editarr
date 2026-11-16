"""Deterministic closing-credits evidence collection and ranking."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from scanner.models import (
    CreditsCandidate,
    CreditsConfidenceBand,
    CreditsDetectionConfiguration,
    CreditsDetectionMethod,
    CreditsDetectionResult,
    CreditsEvidence,
    CreditsEvidenceKind,
)
from scanner.pipeline.processes import ScanCancelledError
from scanner.pipeline.qwen_summary import QWEN_MODELS

DETECTOR_VERSION = "credits-detector-v1"
EVIDENCE_VERSION = "credits-evidence-v1"
THRESHOLD_VERSION = "credits-thresholds-v1"
PROMPT_VERSION = "credits-qwen-prompt-v1"
SCENE_CACHE_VERSION = "scene-boundaries-v1"
FRAME_CACHE_VERSION = "credits-tail-frames-v1"

_EVIDENCE_ORDER = {kind: index for index, kind in enumerate(CreditsEvidenceKind)}
_CREDITS_CHAPTER = re.compile(
    r"(?:closing|end|final)?\s*(?:credits?|cast\s*(?:and|&)\s*crew|cr[eé]ditos|g[eé]n[eé]rique)",
    re.IGNORECASE,
)
_CHAPTER_CONFLICT = re.compile(r"(?:opening|intro|beginning)", re.IGNORECASE)
_EVIDENCE_WINDOW_SECONDS = 8.0
_SCENE_MATCH_SECONDS = 2.0
_TEXT_THRESHOLD = 0.5
_MOTION_THRESHOLD = 0.5
_BLACK_THRESHOLD = 0.85


@dataclass(frozen=True)
class ChapterMarker:
    start_seconds: float
    title: str


@dataclass(frozen=True)
class FrameObservation:
    timestamp_seconds: float
    text_density: float
    credit_motion: float
    black_frame: float


@dataclass(frozen=True)
class QwenAssessment:
    timestamp_seconds: float
    score: float
    details: str


def default_configuration(*, qwen_model: str | None = None) -> CreditsDetectionConfiguration:
    """Return the evaluated v1 detector configuration."""
    return CreditsDetectionConfiguration(
        detector_version=DETECTOR_VERSION,
        tail_window_seconds=45.0,
        frame_sample_interval_seconds=2.0,
        qwen_model=qwen_model,
    )


def _validate_configuration(configuration: CreditsDetectionConfiguration) -> None:
    if configuration.detector_version != DETECTOR_VERSION:
        raise ValueError("Unsupported credits detector version")
    if configuration.qwen_model is not None and configuration.qwen_model not in QWEN_MODELS:
        raise ValueError("Unsupported Qwen model")


def credits_cache_key(
    *,
    video_hash: str,
    configuration: CreditsDetectionConfiguration,
    scene_cache_version: str = SCENE_CACHE_VERSION,
    frame_cache_version: str = FRAME_CACHE_VERSION,
) -> str:
    """Hash every behavior-affecting detector and model identity."""
    if not video_hash.strip():
        raise ValueError("Video hash is required")
    _validate_configuration(configuration)
    qwen_identity = None
    if configuration.qwen_model is not None:
        try:
            model_id, revision = QWEN_MODELS[configuration.qwen_model]
        except KeyError as exc:
            raise ValueError("Unsupported Qwen model") from exc
        qwen_identity = {
            "model": configuration.qwen_model,
            "model_id": model_id,
            "revision": revision,
        }
    identity = {
        "video_hash": video_hash,
        "configuration": configuration.model_dump(mode="json"),
        "detector_version": DETECTOR_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "threshold_version": THRESHOLD_VERSION,
        "prompt_version": PROMPT_VERSION,
        "qwen": qwen_identity,
        "scene_cache_version": scene_cache_version,
        "frame_cache_version": frame_cache_version,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite_unit(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return float(value)


def _timestamp(value: float, duration_seconds: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    if not 0.0 <= value <= duration_seconds:
        raise ValueError(f"{field} must be within the media duration")
    return float(value)


def _is_credible_chapter(
    chapter: ChapterMarker, tail_start: float, duration_seconds: float
) -> bool:
    title = chapter.title.strip()
    return (
        tail_start <= chapter.start_seconds < duration_seconds
        and bool(_CREDITS_CHAPTER.fullmatch(title))
        and not _CHAPTER_CONFLICT.search(title)
    )


def _sort_evidence(evidence: Iterable[CreditsEvidence]) -> list[CreditsEvidence]:
    return sorted(
        evidence,
        key=lambda item: (
            -abs(item.score),
            _EVIDENCE_ORDER[item.kind],
            item.timestamp_seconds,
        ),
    )


def _band(confidence: float) -> CreditsConfidenceBand:
    if confidence < 0.6:
        return CreditsConfidenceBand.LOW
    if confidence < 0.8:
        return CreditsConfidenceBand.MEDIUM
    return CreditsConfidenceBand.HIGH


def _cluster_anchors(anchors: Sequence[float]) -> list[list[float]]:
    clusters: list[list[float]] = []
    for anchor in sorted(set(anchors)):
        if not clusters or anchor - clusters[-1][-1] > _EVIDENCE_WINDOW_SECONDS:
            clusters.append([anchor])
        else:
            clusters[-1].append(anchor)
    return clusters


def _candidate_id(boundary_seconds: float) -> str:
    token = f"{boundary_seconds:.6f}".rstrip("0").rstrip(".").replace(".", "-")
    return f"credits-{token}"


def detect_from_observations(
    *,
    duration_seconds: float,
    configuration: CreditsDetectionConfiguration,
    chapters: Sequence[ChapterMarker] = (),
    scene_boundaries: Sequence[float] = (),
    observations: Sequence[FrameObservation] = (),
    qwen_assessments: Sequence[QwenAssessment] = (),
    should_cancel: Callable[[], bool] | None = None,
) -> CreditsDetectionResult:
    """Rank closing-credits candidates from already extracted bounded evidence."""
    _validate_configuration(configuration)
    if qwen_assessments and configuration.qwen_model is None:
        raise ValueError("Qwen evidence requires an enabled Qwen model")
    if (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
    ):
        raise ValueError("Media duration must be finite and positive")
    duration_seconds = float(duration_seconds)
    tail_start = max(0.0, duration_seconds - configuration.tail_window_seconds)

    credible_chapters: list[ChapterMarker] = []
    for chapter in chapters:
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        timestamp = _timestamp(chapter.start_seconds, duration_seconds, "Chapter timestamp")
        normalized = ChapterMarker(timestamp, chapter.title)
        if _is_credible_chapter(normalized, tail_start, duration_seconds):
            credible_chapters.append(normalized)

    normalized_scenes: list[float] = []
    for value in scene_boundaries:
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        timestamp = _timestamp(value, duration_seconds, "Scene boundary")
        if timestamp >= tail_start:
            normalized_scenes.append(timestamp)
    normalized_observations: list[FrameObservation] = []
    for observation in observations:
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        timestamp = _timestamp(
            observation.timestamp_seconds, duration_seconds, "Observation timestamp"
        )
        if timestamp < tail_start or timestamp >= duration_seconds:
            continue
        normalized_observations.append(
            FrameObservation(
                timestamp_seconds=timestamp,
                text_density=_finite_unit(observation.text_density, "Text density"),
                credit_motion=_finite_unit(observation.credit_motion, "Credit motion"),
                black_frame=_finite_unit(observation.black_frame, "Black frame score"),
            )
        )

    normalized_qwen: list[QwenAssessment] = []
    for assessment in qwen_assessments:
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        timestamp = _timestamp(assessment.timestamp_seconds, duration_seconds, "Qwen timestamp")
        score = _finite_unit(assessment.score, "Qwen score")
        if tail_start <= timestamp < duration_seconds:
            normalized_qwen.append(QwenAssessment(timestamp, score, assessment.details))

    visual_anchors = [
        item.timestamp_seconds
        for item in normalized_observations
        if item.text_density >= _TEXT_THRESHOLD or item.credit_motion >= _MOTION_THRESHOLD
    ]
    anchors = [item.start_seconds for item in credible_chapters] + visual_anchors
    candidates: list[CreditsCandidate] = []
    for cluster in _cluster_anchors(anchors):
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        boundary = min(cluster)
        cluster_end = min(duration_seconds, max(cluster) + _EVIDENCE_WINDOW_SECONDS)
        evidence: list[CreditsEvidence] = []
        for chapter in credible_chapters:
            if abs(chapter.start_seconds - boundary) <= _EVIDENCE_WINDOW_SECONDS:
                evidence.append(
                    CreditsEvidence(
                        kind=CreditsEvidenceKind.CHAPTER_MARKER,
                        timestamp_seconds=chapter.start_seconds,
                        score=0.92,
                        detail_code="credible_credits_chapter",
                        details=chapter.title,
                    )
                )
        for scene_boundary in normalized_scenes:
            if abs(scene_boundary - boundary) <= _SCENE_MATCH_SECONDS:
                evidence.append(
                    CreditsEvidence(
                        kind=CreditsEvidenceKind.SCENE_BOUNDARY,
                        timestamp_seconds=scene_boundary,
                        score=0.55,
                        detail_code="tail_scene_transition",
                        details=None,
                    )
                )
        for observation in normalized_observations:
            if not boundary <= observation.timestamp_seconds <= cluster_end:
                continue
            if observation.text_density >= _TEXT_THRESHOLD:
                evidence.append(
                    CreditsEvidence(
                        kind=CreditsEvidenceKind.TEXT_DENSITY,
                        timestamp_seconds=observation.timestamp_seconds,
                        score=observation.text_density,
                        detail_code="credit_text_pattern",
                        details=None,
                    )
                )
            if observation.credit_motion >= _MOTION_THRESHOLD:
                evidence.append(
                    CreditsEvidence(
                        kind=CreditsEvidenceKind.CREDIT_MOTION,
                        timestamp_seconds=observation.timestamp_seconds,
                        score=observation.credit_motion,
                        detail_code="credit_motion_pattern",
                        details=None,
                    )
                )
            if observation.black_frame >= _BLACK_THRESHOLD:
                evidence.append(
                    CreditsEvidence(
                        kind=CreditsEvidenceKind.BLACK_FRAME,
                        timestamp_seconds=observation.timestamp_seconds,
                        score=-observation.black_frame,
                        detail_code="intermittent_black_frame",
                        details=None,
                    )
                )
        for assessment in normalized_qwen:
            if boundary <= assessment.timestamp_seconds <= cluster_end:
                evidence.append(
                    CreditsEvidence(
                        kind=CreditsEvidenceKind.QWEN,
                        timestamp_seconds=assessment.timestamp_seconds,
                        score=assessment.score,
                        detail_code="qwen_credits_assessment",
                        details=assessment.details,
                    )
                )

        positive_kinds = {
            item.kind
            for item in evidence
            if item.score > 0
            and item.kind not in {CreditsEvidenceKind.BLACK_FRAME, CreditsEvidenceKind.QWEN}
        }
        has_chapter = CreditsEvidenceKind.CHAPTER_MARKER in positive_kinds
        independent_visual_kinds = positive_kinds - {CreditsEvidenceKind.CHAPTER_MARKER}
        if not has_chapter and len(independent_visual_kinds) < 2:
            continue

        positive_scores = [
            item.score
            for item in evidence
            if item.score > 0 and item.kind != CreditsEvidenceKind.QWEN
        ]
        confidence = sum(positive_scores) / len(positive_scores)
        qwen_scores = [
            item.score
            for item in evidence
            if item.score > 0 and item.kind == CreditsEvidenceKind.QWEN
        ]
        if qwen_scores and max(qwen_scores) > confidence:
            confidence = (confidence + max(qwen_scores)) / 2
        conflicts = [abs(item.score) for item in evidence if item.score < 0]
        if conflicts:
            confidence *= max(0.5, 1.0 - 0.15 * sum(conflicts))
        confidence = min(max(confidence, 0.0), 1.0)
        has_visual = bool(
            positive_kinds
            & {
                CreditsEvidenceKind.TEXT_DENSITY,
                CreditsEvidenceKind.CREDIT_MOTION,
                CreditsEvidenceKind.SCENE_BOUNDARY,
            }
        )
        has_qwen = any(item.kind == CreditsEvidenceKind.QWEN for item in evidence)
        confidence = max(confidence, 0.95) if has_chapter else min(confidence, 0.9)
        method = (
            CreditsDetectionMethod.HYBRID_QWEN
            if has_qwen
            else CreditsDetectionMethod.HYBRID
            if has_chapter and has_visual
            else CreditsDetectionMethod.CHAPTERS
            if has_chapter
            else CreditsDetectionMethod.VISUAL_TEXT
        )
        candidates.append(
            CreditsCandidate(
                id=_candidate_id(boundary),
                boundary_seconds=boundary,
                range_start_seconds=boundary,
                range_end_seconds=cluster_end,
                confidence=confidence,
                confidence_band=_band(confidence),
                method=method,
                evidence=_sort_evidence(evidence),
            )
        )

    candidates.sort(key=lambda item: (-item.confidence, item.boundary_seconds, item.id))
    if not candidates:
        return CreditsDetectionResult(
            outcome="no_candidate", candidates=[], best_candidate_id=None
        )
    return CreditsDetectionResult(
        outcome="candidate",
        candidates=candidates,
        best_candidate_id=candidates[0].id,
    )
