"""`scan-<video model>`: the decision detectors Editarr ships, exactly as a scan runs them.

Each id wraps `scanner.pipeline.decision_detection.detector_for`, so prompts, weights,
thresholds and (for d1-3B) rubric severity come from production code and the pinned,
hash-verified artifacts. Missing artifacts are reported as unavailable unless the run
allows downloads (`--allow-downloads`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scanner.pipeline import decision_detection

from ..backends import BackendUnavailable, downloads_allowed, require_local_artifacts

PREFIX = "scan-"
# Shipped detectors that rate rubric severity; their frames always carry a severity map,
# empty when every answer was unusable, so describe-eval counts those as unusable.
SEVERITY_MODELS = frozenset({"d1-3b"})


class ScanDetectorBackend:
    """One shipped decision detector; frames are scored one at a time."""

    def __init__(self, video_model: str) -> None:
        self.video_model = video_model
        self.model_id = f"{PREFIX}{video_model}"
        self.detector = decision_detection.detector_for(video_model)
        self._lock = threading.Lock()

    @property
    def runtime(self) -> str:
        return f"Editarr {self.detector.label_prefix} detector"

    def check_available(self) -> None:
        """Raise BackendUnavailable unless the capability is usable and weights are local
        (or, with downloads allowed, could be acquired)."""
        from scanner.model_acquisition import get_acquisition_service
        from scanner.settings.capabilities import capability_for

        capability = capability_for(self.video_model)
        if capability.availability == "unavailable":
            raise BackendUnavailable(f"unavailable: {self.video_model}: {capability.reason}")
        artifact_id = _artifact_id(self.video_model)
        if not downloads_allowed():
            require_local_artifacts((artifact_id,))
            return
        try:
            get_acquisition_service().acquire(artifact_id)
        except Exception as exc:  # noqa: BLE001 - any acquisition failure disables the model
            raise BackendUnavailable(f"unavailable: {artifact_id}: {exc}") from exc

    def meta(self) -> dict[str, Any]:
        thresholds = _thresholds(self.video_model)
        return {
            "model_id": self.model_id,
            "display_name": f"{self.detector.model_id} (Editarr scan detector)",
            "supports": sorted(family.value for family in decision_detection.DETECTED_FAMILIES),
            "thresholds": {**thresholds, "*": 0.5},
            "runtime": self.runtime,
            "artifact": f"{self.detector.model_id}@{self.detector.model_revision}",
            "notes": (
                "Production decision detector: one yes/no question per family per frame"
                + (", plus rubric severity levels." if self.video_model in SEVERITY_MODELS else ".")
            ),
        }

    def score_frames(self, paths: list[Path]) -> list[dict[str, Any]]:
        outputs = []
        with self._lock:
            for path in paths:
                decision = self.detector.score(Path(path))
                frame: dict[str, Any] = {
                    "scores": {family.value: score for family, score in decision.scores.items()},
                    "labels": [],
                }
                if self.video_model in SEVERITY_MODELS:
                    frame["severity"] = {
                        family.value: level for family, level in decision.levels.items()
                    }
                outputs.append(frame)
        return outputs

    def close(self) -> None:
        from scanner.pipeline.detector_memory import release_detector

        release_detector(self.video_model)


def _artifact_id(video_model: str) -> str:
    if video_model == "d1-3b":
        from scanner.pipeline import d1_detector

        return d1_detector.ARTIFACT_ID
    from scanner.pipeline import qwen_detector

    return qwen_detector.ARTIFACTS[video_model]


def _thresholds(video_model: str) -> dict[str, float]:
    if video_model == "d1-3b":
        from scanner.pipeline import d1_detector as module
    else:
        from scanner.pipeline import qwen_detector as module
    return {family.value: threshold for family, threshold in module.THRESHOLDS.items()}


def _factory(video_model: str) -> Callable[[], ScanDetectorBackend]:
    return lambda: ScanDetectorBackend(video_model)


BACKENDS: dict[str, Callable[[], ScanDetectorBackend]] = {
    f"{PREFIX}{video_model}": _factory(video_model)
    for video_model in decision_detection.DECISION_VIDEO_MODELS
}
