"""Model backends that score cached frames with MCF keys."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

DECISION_MODEL_IDS = ("d1-omni-2b", "clef-flash", "d1-3b")


class Backend(Protocol):
    model_id: str

    def meta(self) -> dict[str, Any]:
        """Return the `models/<id>.meta.json` content."""

    def score_frames(self, paths: list[Path]) -> list[dict[str, Any]]:
        """Return one `{"scores": {key: prob}, "labels": [...]}` per frame."""

    def close(self) -> None: ...


class BackendUnavailable(RuntimeError):
    pass


def _detector_key(producer: str, family: Any, label: str, confidence: float) -> str:
    """Map a detector label to `family` or `family/token` with the repo's MCF mapping."""
    from scanner.evaluation.subcategorization import (
        SubcategorizationMethod,
        subcategorize_detector_label,
    )

    result = subcategorize_detector_label(
        producer=producer, category=family, label=label, confidence=confidence
    )
    if result.method == SubcategorizationMethod.GENERAL_FALLBACK:
        return result.category.value
    return f"{result.category.value}/{result.subcategory.value}"


class NudeNetBackend:
    """NudeNet 320n via the scanner's cached detector and configured risk labels."""

    model_id = "nudenet"

    def __init__(self) -> None:
        from scanner.models import McfCategoryFamily
        from scanner.pipeline.visual import _get_detector
        from scanner.settings.config import settings

        self._family = McfCategoryFamily.NUDITY
        self._detector = _get_detector()
        self._risk_labels = set(settings.nudity_risk_labels)
        self._threshold = settings.nudity_confidence_threshold
        self._batch_size = max(1, settings.visual_batch_size)
        self._supports = sorted(
            {"nudity"}
            | {
                _detector_key("nudenet", self._family, label, 1.0)
                for label in self._risk_labels
            }
        )

    def meta(self) -> dict[str, Any]:
        from scanner.settings.capabilities import capability_for

        return {
            "model_id": self.model_id,
            "display_name": "NudeNet 320n",
            "supports": self._supports,
            "thresholds": {"*": self._threshold},
            "runtime": f"onnxruntime ({capability_for('nudenet').device})",
            "artifact": "nudenet/320n.onnx",
            "notes": (
                "Frame score per key = max detection confidence among configured risk "
                f"labels {sorted(self._risk_labels)}; labels lists every raw detection."
            ),
        }

    def score_frames(self, paths: list[Path]) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for start in range(0, len(paths), self._batch_size):
            batch = [str(path) for path in paths[start : start + self._batch_size]]
            detections = self._detector.detect_batch(batch, batch_size=len(batch))
            if len(detections) != len(batch):
                raise RuntimeError("NudeNet returned an unexpected number of results")
            for frame_detections in detections:
                scores = dict.fromkeys(self._supports, 0.0)
                labels = []
                for detection in frame_detections:
                    label, confidence = detection["class"], float(detection["score"])
                    labels.append(f"{label} {confidence:.2f}")
                    if label not in self._risk_labels:
                        continue
                    key = _detector_key("nudenet", self._family, label, confidence)
                    for target in {key, "nudity"}:
                        scores[target] = max(scores.get(target, 0.0), confidence)
                outputs.append({"scores": scores, "labels": labels})
        return outputs

    def close(self) -> None:
        return None


class Siglip2Backend:
    """SigLIP2 encoder with the NSFW (→ nudity) and violence moderation heads."""

    model_id = "siglip2"
    _HEADS = {"nsfw": "nudity", "violence": "violence"}

    def __init__(self) -> None:
        from scanner.pipeline import siglip2_moderation
        from scanner.settings.config import settings

        self._module = siglip2_moderation
        self._batch_size = max(1, settings.visual_batch_size)
        self._thresholds = {
            key: siglip2_moderation.threshold_for_head(head) for head, key in self._HEADS.items()
        }
        siglip2_moderation._load_encoder()
        for head in self._HEADS:
            siglip2_moderation._load_head(head)

    def meta(self) -> dict[str, Any]:
        from scanner.settings.capabilities import capability_for

        return {
            "model_id": self.model_id,
            "display_name": "SigLIP2 moderation heads",
            "supports": sorted(self._HEADS.values()),
            "thresholds": {**self._thresholds, "*": 0.5},
            "runtime": (
                f"torch ({capability_for('siglip2_moderation_nsfw').device}) + onnxruntime CPU"
            ),
            "artifact": "google/siglip2-base-patch16-256 + khasinski/siglip2-moderation-heads",
            "notes": "nsfw head maps to nudity, violence head to violence (as in visual.py).",
        }

    def score_frames(self, paths: list[Path]) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = [{"scores": {}} for _ in paths]
        for start in range(0, len(paths), self._batch_size):
            batch = paths[start : start + self._batch_size]
            for head, key in self._HEADS.items():
                probabilities = self._module.score_images(batch, head)
                for offset, probability in enumerate(probabilities):
                    outputs[start + offset]["scores"][key] = probability
        return outputs

    def close(self) -> None:
        return None


def _siglip2_gore() -> Backend:
    from .siglip2_gore import Siglip2GoreBackend

    return Siglip2GoreBackend()


LOCAL_BACKENDS: dict[str, Callable[[], Backend]] = {
    "nudenet": NudeNetBackend,
    "siglip2": Siglip2Backend,
    "siglip2-gore": _siglip2_gore,
}


def _decision_backends() -> dict[str, Callable[[], Backend]]:
    try:
        from .decision.registry import BACKENDS
    except ImportError as exc:
        raise BackendUnavailable(f"decision backends unavailable ({exc})") from exc
    return BACKENDS


def available_model_ids() -> list[str]:
    ids = list(LOCAL_BACKENDS)
    try:
        ids += [model_id for model_id in _decision_backends() if model_id not in ids]
    except BackendUnavailable:
        pass
    return ids


def create_backend(model_id: str) -> Backend:
    """Instantiate a backend by id; decision models load lazily from `decision.registry`."""
    if model_id in LOCAL_BACKENDS:
        return LOCAL_BACKENDS[model_id]()
    try:
        backends = _decision_backends()
    except BackendUnavailable:
        if model_id in DECISION_MODEL_IDS:
            raise
        raise BackendUnavailable(f"Unknown model {model_id!r}") from None
    if model_id not in backends:
        known = sorted(set(LOCAL_BACKENDS) | set(backends))
        raise BackendUnavailable(f"Unknown model {model_id!r}; known: {', '.join(known)}")
    backend = backends[model_id]()
    # Fail before any item is attempted when the binary or weights are missing.
    check = getattr(backend, "check_available", None)
    if check is not None:
        check()
    return backend
