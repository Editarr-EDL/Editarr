"""Visual frame scanning with the configured detection backend."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from scanner.evaluation.subcategorization import (
    subcategorize_detector_label,
)
from scanner.models import FindingCreate, McfCategoryFamily, Severity
from scanner.pipeline.visual_summary import (
    _flagged_segments as _flagged_segments,
)
from scanner.pipeline.visual_summary import (
    _FlaggedSegment as _FlaggedSegment,
)
from scanner.pipeline.visual_summary import (
    _is_no_objectionable_content as _is_no_objectionable_content,
)
from scanner.pipeline.visual_summary import (
    _replay_finding as _replay_finding,
)
from scanner.pipeline.visual_summary import (
    _representative_frames as _representative_frames,
)
from scanner.pipeline.visual_summary import (
    _save_qwen_replay as _save_qwen_replay,
)
from scanner.pipeline.visual_summary import (
    _summarized_findings as _summarized_findings,
)
from scanner.pipeline.visual_summary import (
    replay_qwen_summaries as replay_qwen_summaries,
)
from scanner.pipeline.visual_summary import (
    summarize_flagged_segments as summarize_flagged_segments,
)
from scanner.settings.capabilities import (
    capability_for,
)
from scanner.settings.capabilities import qwen_enrichment_capability as qwen_enrichment_capability
from scanner.settings.config import settings

from .inference_devices import CPU_PROVIDER, create_coreml_session

if TYPE_CHECKING:
    from nudenet import NudeDetector

    from scanner.pipeline.image_safety_gate import ImageSafetyGateResult


@dataclass
class VisualDetection:
    timestamp: float
    label: str
    confidence: float
    category: McfCategoryFamily
    subcategory: str
    severity: Severity


_detector: NudeDetector | None = None
_SEVERITY_ORDER = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}

SUPPORTED_VIDEO_MODELS = {
    "nudenet",
    "image_safety_gate",
    "siglip2_moderation_nsfw",
    "siglip2_moderation_violence",
    "shieldstral",
}
_VIDEO_MODEL_ALIASES = {
    "image-safety-gate": "image_safety_gate",
    "comfyui-image-safety-gate": "image_safety_gate",
    "comfyui_image_safety_gate": "image_safety_gate",
    "siglip2-moderation-nsfw": "siglip2_moderation_nsfw",
    "siglip2-moderation-violence": "siglip2_moderation_violence",
    "shieldstral-1.0-3b": "shieldstral",
}


def normalize_video_model(video_model: str | None) -> str:
    selected = (video_model or "nudenet").strip().lower()
    resolved = _VIDEO_MODEL_ALIASES.get(selected, selected)
    if resolved not in SUPPORTED_VIDEO_MODELS:
        raise ValueError(f"Unsupported video model: {video_model}")
    return resolved


def _get_detector() -> NudeDetector:
    """Load and cache NudeNet with the available execution provider."""
    import onnxruntime
    from nudenet import NudeDetector

    global _detector
    if _detector is None:
        detector = NudeDetector(providers=[CPU_PROVIDER])
        if capability_for("nudenet").device == "coreml":
            # NudeNet 3.4.2 ignores providers in the constructor. Configure its session.
            model_path = Path(str(files("nudenet").joinpath("320n.onnx")))
            detector.onnx_session = create_coreml_session(
                onnxruntime,
                model_path,
                dimensions={"height": detector.input_height, "width": detector.input_width},
                cache_dir=settings.model_cache / "nudenet" / "coreml-cache",
            )
        _detector = detector
    return _detector


def _confidence_to_severity(confidence: float) -> Severity:
    if confidence >= 0.85:
        return Severity.HIGH
    if confidence >= 0.65:
        return Severity.MEDIUM
    return Severity.LOW


def _clamp_confidence(confidence: float) -> float:
    return max(0.0, min(confidence, 1.0))


def _image_safety_gate_to_detections(
    timestamp: float,
    result: "ImageSafetyGateResult",
) -> list[VisualDetection]:
    """Map image safety scores into timestamped visual detections."""
    detections: list[VisualDetection] = []
    nsfw_confidence = _clamp_confidence(max(result.wd_total, result.clip_confidence))
    if result.wd_unsafe or result.clip_unsafe:
        classification = subcategorize_detector_label(
            producer="image_safety_gate",
            category=McfCategoryFamily.NUDITY,
            label="image_safety_gate_nsfw",
            confidence=nsfw_confidence,
        )
        detections.append(
            VisualDetection(
                timestamp=timestamp,
                label="image_safety_gate_nsfw",
                confidence=nsfw_confidence,
                category=classification.category,
                subcategory=classification.subcategory.value,
                severity=_confidence_to_severity(nsfw_confidence),
            )
        )
    if result.nsfl_unsafe:
        violence_confidence = _clamp_confidence(result.nsfl_score)
        classification = subcategorize_detector_label(
            producer="image_safety_gate",
            category=McfCategoryFamily.VIOLENCE,
            label="image_safety_gate_nsfl",
            confidence=violence_confidence,
        )
        detections.append(
            VisualDetection(
                timestamp=timestamp,
                label="image_safety_gate_nsfl",
                confidence=violence_confidence,
                category=classification.category,
                subcategory=classification.subcategory.value,
                severity=_confidence_to_severity(violence_confidence),
            )
        )
    return detections


def _scan_frame_nudenet(frame_path: Path, timestamp: float) -> list[VisualDetection]:
    """Scan a single frame for nudity/explicit content with NudeNet."""
    detector = _get_detector()
    detections = detector.detect(str(frame_path))

    results: list[VisualDetection] = []
    for det in detections:
        label = det["class"]
        confidence = det["score"]

        if label not in settings.nudity_risk_labels:
            continue
        if confidence < settings.nudity_confidence_threshold:
            continue

        classification = subcategorize_detector_label(
            producer="nudenet",
            category=McfCategoryFamily.NUDITY,
            label=label,
            confidence=confidence,
            model_id="nudenet/320n.onnx",
            model_revision="nudenet>=3.4.2/320n.onnx",
        )

        results.append(
            VisualDetection(
                timestamp=timestamp,
                label=label,
                confidence=confidence,
                category=classification.category,
                subcategory=classification.subcategory.value,
                severity=_confidence_to_severity(confidence),
            )
        )

    return results


def _scan_frames_nudenet(frames: list[tuple[Path, float]]) -> Iterator[list[VisualDetection]]:
    """Batch NudeNet detections while retaining one result list per source frame."""
    batch_size = settings.visual_batch_size
    if batch_size < 1:
        raise ValueError("Visual batch size must be at least one")
    detector = _get_detector()
    for start_index in range(0, len(frames), batch_size):
        batch = frames[start_index : start_index + batch_size]
        detections_by_frame = detector.detect_batch(
            [str(frame_path) for frame_path, _timestamp in batch], batch_size=len(batch)
        )
        if len(detections_by_frame) != len(batch):
            raise RuntimeError("NudeNet returned an unexpected number of detection results")
        for (_frame_path, timestamp), detections in zip(batch, detections_by_frame, strict=True):
            results: list[VisualDetection] = []
            for detection in detections:
                label = detection["class"]
                confidence = detection["score"]
                if label not in settings.nudity_risk_labels:
                    continue
                if confidence < settings.nudity_confidence_threshold:
                    continue
                classification = subcategorize_detector_label(
                    producer="nudenet",
                    category=McfCategoryFamily.NUDITY,
                    label=label,
                    confidence=confidence,
                    model_id="nudenet/320n.onnx",
                    model_revision="nudenet>=3.4.2/320n.onnx",
                )
                results.append(
                    VisualDetection(
                        timestamp=timestamp,
                        label=label,
                        confidence=confidence,
                        category=classification.category,
                        subcategory=classification.subcategory.value,
                        severity=_confidence_to_severity(confidence),
                    )
                )
            yield results


def _scan_frame_image_safety_gate(frame_path: Path, timestamp: float) -> list[VisualDetection]:
    from scanner.pipeline.image_safety_gate import scan_image_safety_gate

    result = scan_image_safety_gate(frame_path)
    return _image_safety_gate_to_detections(timestamp, result)


def _scan_frame_siglip2_moderation(
    frame_path: Path, timestamp: float, head_name: str
) -> list[VisualDetection]:
    """Classify one frame with the selected SigLIP2 moderation head."""
    from scanner.pipeline.siglip2_moderation import score_image, threshold_for_head

    confidence = score_image(frame_path, head_name)
    if confidence < threshold_for_head(head_name):
        return []
    category = (
        McfCategoryFamily.NUDITY
        if head_name == "nsfw"
        else McfCategoryFamily.VIOLENCE
    )
    classification = subcategorize_detector_label(
        producer=f"siglip2_moderation_{head_name}",
        category=category,
        label=f"siglip2_moderation_{head_name}",
        confidence=confidence,
    )
    return [
        VisualDetection(
            timestamp=timestamp,
            label=f"siglip2_moderation_{head_name}",
            confidence=confidence,
            category=classification.category,
            subcategory=classification.subcategory.value,
            severity=_confidence_to_severity(confidence),
        )
    ]


def _scan_frames_siglip2_moderation(
    frames: list[tuple[Path, float]], head_name: str
) -> Iterator[list[VisualDetection]]:
    """Batch SigLIP2 scoring while retaining one detection list per source frame."""
    from scanner.pipeline.siglip2_moderation import score_images, threshold_for_head

    batch_size = settings.visual_batch_size
    if batch_size < 1:
        raise ValueError("Visual batch size must be at least one")
    threshold = threshold_for_head(head_name)
    for start_index in range(0, len(frames), batch_size):
        batch = frames[start_index : start_index + batch_size]
        confidences = score_images([frame_path for frame_path, _timestamp in batch], head_name)
        if len(confidences) != len(batch):
            raise RuntimeError("SigLIP2 returned an unexpected number of scores")
        for (_frame_path, timestamp), confidence in zip(batch, confidences, strict=True):
            if confidence < threshold:
                yield []
                continue
            category = (
                McfCategoryFamily.NUDITY
                if head_name == "nsfw"
                else McfCategoryFamily.VIOLENCE
            )
            classification = subcategorize_detector_label(
                producer=f"siglip2_moderation_{head_name}",
                category=category,
                label=f"siglip2_moderation_{head_name}",
                confidence=confidence,
            )
            yield [
                VisualDetection(
                    timestamp=timestamp,
                    label=f"siglip2_moderation_{head_name}",
                    confidence=confidence,
                    category=classification.category,
                    subcategory=classification.subcategory.value,
                    severity=_confidence_to_severity(confidence),
                )
            ]


def _findings_from_detections(
    detections: list[VisualDetection],
    *,
    video_id: str,
    frame_interval_seconds: float,
    video_duration_seconds: float | None,
) -> list[FindingCreate]:
    """Normalize detector outputs into reviewable scan findings."""
    findings: list[FindingCreate] = []
    for det in detections:
        end_seconds = det.timestamp + frame_interval_seconds
        if video_duration_seconds is not None:
            end_seconds = min(end_seconds, video_duration_seconds)
        findings.append(
            FindingCreate(
                video_id=video_id,
                category=det.category,
                subcategory=det.subcategory,
                start_seconds=det.timestamp,
                end_seconds=end_seconds,
                severity=det.severity,
                confidence=det.confidence,
                label=det.label,
                details=f"Detected {det.label} at {det.timestamp:.1f}s",
                group_key=det.category.value,
                source="visual",
            )
        )
    return findings


def scan_frame(
    frame_path: Path,
    timestamp: float,
    video_model: str | None = None,
) -> list[VisualDetection]:
    """Scan a single frame using the selected visual model."""
    selected_model = normalize_video_model(video_model)
    if selected_model == "image_safety_gate":
        return _scan_frame_image_safety_gate(frame_path, timestamp)
    if selected_model == "shieldstral":
        from scanner.pipeline import shieldstral

        return shieldstral.scan_frame(frame_path, timestamp)
    if selected_model.startswith("siglip2_moderation_"):
        return _scan_frame_siglip2_moderation(
            frame_path,
            timestamp,
            selected_model.removeprefix("siglip2_moderation_"),
        )
    return _scan_frame_nudenet(frame_path, timestamp)


def scan_frames(
    frames: list[tuple[Path, float]],
    video_id: str,
    video_model: str | None = None,
    frame_interval_seconds: float | None = None,
    video_duration_seconds: float | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[FindingCreate]:
    """Run the safety model over a batch of frames and return its findings.

    Qwen is deliberately not called here.  A scene's complete set of safety
    findings is needed before representative frames can be selected for a
    much smaller, deferred Qwen pass.
    """
    findings: list[FindingCreate] = []
    interval = frame_interval_seconds or settings.frame_interval_seconds
    selected_model = normalize_video_model(video_model)

    total = len(frames)
    if selected_model == "nudenet":
        detection_lists = _scan_frames_nudenet(frames)
    elif selected_model.startswith("siglip2_moderation_"):
        detection_lists = _scan_frames_siglip2_moderation(
            frames, selected_model.removeprefix("siglip2_moderation_")
        )
    elif selected_model == "shieldstral":
        from scanner.pipeline import shieldstral

        detection_lists = shieldstral.scan_frames(frames, interval)
    else:
        detection_lists = (
            scan_frame(frame_path, timestamp, video_model=selected_model)
            for frame_path, timestamp in frames
        )

    for index, detections in enumerate(detection_lists, start=1):
        findings.extend(
            _findings_from_detections(
                detections,
                video_id=video_id,
                frame_interval_seconds=interval,
                video_duration_seconds=video_duration_seconds,
            )
        )
        if progress_callback:
            progress_callback(index, total)

    return findings
