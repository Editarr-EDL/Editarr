"""Opt-in real-model inference probe for a frozen runtime.

Release verification on a host that already has a pinned artifact cached can ask
the frozen runtime smoke to run one real frame through that model. The probe never
acquires a model: an artifact that is not already ready fails the smoke.
"""

from __future__ import annotations

import time
from pathlib import Path

MODEL_ENVIRONMENT = "EDITARR_FROZEN_RUNTIME_SMOKE_MODEL"
FRAME_ENVIRONMENT = "EDITARR_FROZEN_RUNTIME_SMOKE_FRAME"
PROBED_MODELS = ("d1-3b",)


def run_model_probe(model: str, frame: Path) -> dict[str, object]:
    """Score ``frame`` twice with the cached ``model`` and report decisions and timing.

    The first call includes loading the weights; the second is a warm inference.
    """
    if model not in PROBED_MODELS:
        raise RuntimeError(f"Unsupported frozen model probe {model!r}")
    if not frame.is_file():
        raise RuntimeError(f"Frozen model probe frame {frame} does not exist")
    from scanner.model_acquisition import get_acquisition_service
    from scanner.pipeline import d1_detector

    state = get_acquisition_service().status(d1_detector.ARTIFACT_ID).state
    if state != "ready":
        raise RuntimeError(f"{model} is {state} in the model cache; the probe never downloads")
    import torch

    timings = []
    try:
        for _ in range(2):
            started = time.monotonic()
            decision = d1_detector.score_frame(frame)
            timings.append(round(time.monotonic() - started, 2))
    finally:
        d1_detector.release()
    return {
        "model": model,
        "revision": d1_detector.MODEL_REVISION,
        "device": d1_detector._device(torch),
        "frame": str(frame),
        "load_and_first_seconds": timings[0],
        "warm_seconds": timings[1],
        "scores": {family.value: round(score, 4) for family, score in decision.scores.items()},
        "positives": [family.value for family in decision.positives()],
        "severity_levels": {family.value: level for family, level in decision.levels.items()},
    }
