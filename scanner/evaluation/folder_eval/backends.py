"""Model backends that score cached frames with MCF keys."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .siglip2_gore import ARTIFACT_ID as GORE_ARTIFACT_ID

DECISION_MODEL_IDS = ("d1-omni-2b", "clef-flash", "d1-3b")
INSTALL_HINT = "install it from Editarr's model settings or rerun with --allow-downloads"
_downloads_allowed = False


class Backend(Protocol):
    model_id: str

    def meta(self) -> dict[str, Any]:
        """Return the `models/<id>.meta.json` content."""

    def score_frames(self, paths: list[Path]) -> list[dict[str, Any]]:
        """Return one `{"scores": {key: prob}, "labels": [...]}` per frame."""

    def close(self) -> None: ...


class BackendUnavailable(RuntimeError):
    pass


def configure_model_downloads(allow_downloads: bool) -> None:
    """Set the harness's model-download policy once, at CLI start before any model threads.

    Unless `allow_downloads`, Editarr's process-wide download refusal is switched on, so
    every acquisition (including loaders reused from `scanner.pipeline`) fails instead of
    downloading. Without this call downloads count as not allowed for the local-artifact
    checks, but Editarr's own refusal stays off.
    """
    global _downloads_allowed
    _downloads_allowed = allow_downloads
    if not allow_downloads:
        from scanner.modeling.model_acquisition_authority import refuse_model_downloads

        refuse_model_downloads()


def downloads_allowed() -> bool:
    """Whether this harness process may download Editarr model artifacts."""
    return _downloads_allowed


def require_local_artifacts(artifact_ids: tuple[str, ...]) -> None:
    """Raise BackendUnavailable for the first artifact that is not installed locally,
    unless downloads are allowed."""
    if downloads_allowed():
        return
    from scanner.model_acquisition import get_acquisition_service

    service = get_acquisition_service()
    for artifact_id in artifact_ids:
        try:
            service.verified_local_artifact(artifact_id, verify_content=False)
        except Exception as exc:  # noqa: BLE001 - any lookup failure disables the model
            raise BackendUnavailable(
                f"unavailable: {artifact_id} is not installed ({exc}); {INSTALL_HINT}"
            ) from exc


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
    artifacts = (
        "siglip2-base-patch16-256",
        "siglip2-moderation-head-nsfw",
        "siglip2-moderation-head-violence",
    )

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
# Editarr artifacts each local backend loads; NudeNet's weights ship with its package.
LOCAL_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "siglip2": Siglip2Backend.artifacts,
    "siglip2-gore": (GORE_ARTIFACT_ID,),
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


def _local_backend(model_id: str) -> Backend:
    """Build a local backend from verified local artifacts (see `configure_model_downloads`)."""
    from scanner.model_acquisition import AcquisitionError

    factory = LOCAL_BACKENDS[model_id]
    require_local_artifacts(LOCAL_ARTIFACTS.get(model_id, ()))
    try:
        return factory()
    except AcquisitionError as exc:
        raise BackendUnavailable(f"unavailable: {model_id}: {exc}; {INSTALL_HINT}") from exc
    except ImportError as exc:
        raise BackendUnavailable(f"unavailable: {model_id}: {exc}") from exc


def create_backend(model_id: str) -> Backend:
    """Instantiate a backend by id; decision models load lazily from `decision.registry`.

    Missing artifacts, refused downloads and acquisition failures raise
    BackendUnavailable, so `run` reports the model unavailable and `all` skips it.
    """
    if model_id in LOCAL_BACKENDS:
        return _local_backend(model_id)
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
