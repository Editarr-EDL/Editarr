"""Frame decisions from the VLM decision detectors (Qwen and d1-3B).

Each detector answers one yes/no question per family about a frame and reports
P(yes). A family is positive when its score reaches the detector's threshold. Fine
passes score a strided subset of frames and bisect label changes, as Shieldstral does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from scanner.evaluation.severity_rubric import level_severity
from scanner.evaluation.subcategorization import subcategorize_detector_label
from scanner.models import McfCategoryFamily
from scanner.settings.capabilities import QWEN38_27B_VIDEO_MODEL
from scanner.settings.config import settings

from .severity import SEVERITY_CONFIDENCE, SEVERITY_DETECTOR
from .stride_sampling import sample_and_hold
from .visual import VisualDetection, _clamp_confidence, _confidence_to_severity

# Registry order, which is also the evaluated question order.
DETECTED_FAMILIES = (
    McfCategoryFamily.DRUGS,
    McfCategoryFamily.NUDITY,
    McfCategoryFamily.SEX,
    McfCategoryFamily.VIOLENCE,
)
FAMILY_SUBJECTS = {
    McfCategoryFamily.DRUGS: "drugs, alcohol, smoking, or gambling",
    McfCategoryFamily.NUDITY: "nudity",
    McfCategoryFamily.SEX: "sexual content",
    McfCategoryFamily.VIOLENCE: "violence",
}
DECISION_VIDEO_MODELS = ("qwen3-vl-8b", "qwen3-vl-4b", QWEN38_27B_VIDEO_MODEL, "d1-3b")


def family_question(family: McfCategoryFamily) -> str:
    """The evaluated yes/no question for one family."""
    return f"Does this video frame show {FAMILY_SUBJECTS[family]}?"


@dataclass(frozen=True)
class FrameDecision:
    """Per-family P(yes) for one frame, plus rubric severity levels when asked."""

    scores: Mapping[McfCategoryFamily, float]
    thresholds: Mapping[McfCategoryFamily, float]
    levels: Mapping[McfCategoryFamily, int] = field(default_factory=dict)

    def positives(self) -> tuple[McfCategoryFamily, ...]:
        return tuple(
            family
            for family in DETECTED_FAMILIES
            if self.scores.get(family, 0.0) >= self.thresholds[family]
        )

    def key(self) -> frozenset[McfCategoryFamily]:
        return frozenset(self.positives())


@dataclass(frozen=True)
class DecisionDetector:
    """One loaded-on-demand decision backend and its provenance."""

    video_model: str
    label_prefix: str
    model_id: str
    model_revision: str
    score: Callable[[Path], FrameDecision]


def detector_for(video_model: str) -> DecisionDetector:
    """Return the backend for a decision video model without loading weights."""
    if video_model == "d1-3b":
        from . import d1_detector

        return d1_detector.DETECTOR
    if video_model in DECISION_VIDEO_MODELS:
        from . import qwen_detector

        return qwen_detector.detector(video_model)
    raise ValueError(f"Unsupported decision video model: {video_model}")


def detections_for_decision(
    decision: FrameDecision, timestamp: float, detector: DecisionDetector
) -> list[VisualDetection]:
    """Emit one general-family detection per positive family.

    A detector-supplied rubric level sets the severity (``detector`` source); otherwise
    the confidence mapping is used (``confidence`` source).
    """
    detections: list[VisualDetection] = []
    for family in decision.positives():
        confidence = _clamp_confidence(decision.scores[family])
        label = f"{detector.label_prefix}_{family.value}"
        classification = subcategorize_detector_label(
            producer=detector.video_model,
            category=family,
            label=label,
            confidence=confidence,
            model_id=detector.model_id,
            model_revision=detector.model_revision,
        )
        level = decision.levels.get(family)
        detections.append(
            VisualDetection(
                timestamp=timestamp,
                label=label,
                confidence=confidence,
                category=classification.category,
                subcategory=classification.subcategory.value,
                severity=(
                    _confidence_to_severity(confidence) if level is None else level_severity(level)
                ),
                severity_source=SEVERITY_CONFIDENCE if level is None else SEVERITY_DETECTOR,
            )
        )
    return detections


def scan_frame(frame_path: Path, timestamp: float, video_model: str) -> list[VisualDetection]:
    """Score one coarse sample and return its detections."""
    detector = detector_for(video_model)
    return detections_for_decision(detector.score(frame_path), timestamp, detector)


def fine_stride(frame_interval_seconds: float) -> int:
    """Return how many source frames separate strided fine-pass samples."""
    if frame_interval_seconds <= 0:
        return 1
    return max(
        1, round(settings.decision_fine_sample_interval_seconds / frame_interval_seconds)
    )


def _report_resolved(
    results: Iterable[FrameDecision], on_resolved: Callable[[int], None]
) -> Iterator[FrameDecision]:
    for resolved, result in enumerate(results, start=1):
        on_resolved(resolved)
        yield result


def scan_frames(
    frames: list[tuple[Path, float]],
    frame_interval_seconds: float,
    video_model: str,
    on_resolved: Callable[[int], None] | None = None,
) -> Iterator[list[VisualDetection]]:
    """Yield one detection list per frame, scoring a strided subset with refined edges.

    Unscored frames reuse the nearest scored decision within the same uniform run of
    positive families, stamped with their own timestamp.
    """
    detector = detector_for(video_model)
    results: Iterable[FrameDecision] = sample_and_hold(
        len(frames),
        fine_stride(frame_interval_seconds),
        lambda index: detector.score(frames[index][0]),
        FrameDecision.key,
    )
    if on_resolved is not None:
        results = _report_resolved(results, on_resolved)
    for (_frame_path, timestamp), result in zip(frames, results, strict=True):
        yield detections_for_decision(result, timestamp, detector)
