"""Opt-in real-model inference probe for a frozen runtime.

Release verification on a host that already has a pinned artifact cached can ask
the frozen runtime smoke to run one real frame through that model. The probe cannot
download: it resolves the installed artifact once after rehashing it, loads only
from that directory with the Hugging Face Hub offline, never calls an acquisition
function, and fails if the artifact is missing or changes while it loads.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MODEL_ENVIRONMENT = "EDITARR_FROZEN_RUNTIME_SMOKE_MODEL"
FRAME_ENVIRONMENT = "EDITARR_FROZEN_RUNTIME_SMOKE_FRAME"
PROBED_MODELS = ("d1-3b",)
_OFFLINE_ENVIRONMENT = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


@contextmanager
def hub_offline() -> Iterator[None]:
    """Put the Hugging Face Hub in offline mode, restoring the previous mode afterwards.

    huggingface_hub reads ``HF_HUB_OFFLINE`` once at import, so its constant is set
    alongside the environment.
    """
    from huggingface_hub import constants

    saved = {name: os.environ.get(name) for name in _OFFLINE_ENVIRONMENT}
    saved_constant = constants.HF_HUB_OFFLINE
    os.environ.update(dict.fromkeys(_OFFLINE_ENVIRONMENT, "1"))
    constants.HF_HUB_OFFLINE = True
    try:
        yield
    finally:
        constants.HF_HUB_OFFLINE = saved_constant
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_model_probe(model: str, frame: Path) -> dict[str, object]:
    """Score ``frame`` twice with the cached ``model`` and report decisions and timing.

    The first call includes loading the weights; the second is a warm inference.
    """
    if model not in PROBED_MODELS:
        raise RuntimeError(f"Unsupported frozen model probe {model!r}")
    if not frame.is_file():
        raise RuntimeError(f"Frozen model probe frame {frame} does not exist")
    from scanner.model_acquisition import AcquisitionError, get_acquisition_service
    from scanner.pipeline import d1_detector

    service = get_acquisition_service()
    try:
        model_dir = service.verified_local_artifact(d1_detector.ARTIFACT_ID)
    except AcquisitionError as error:
        raise RuntimeError(f"{model}: {error} The probe never downloads.") from error
    import torch

    timings = []
    with hub_offline():
        try:
            for _ in range(2):
                started = time.monotonic()
                decision = d1_detector.score_frame(frame, model_dir=model_dir)
                timings.append(round(time.monotonic() - started, 2))
        finally:
            d1_detector.release()
    try:
        unchanged = service.verified_local_artifact(d1_detector.ARTIFACT_ID, verify_content=False)
    except AcquisitionError as error:
        raise RuntimeError(f"{model} changed in the model cache while the probe ran") from error
    if unchanged != model_dir:
        raise RuntimeError(f"{model} moved in the model cache while the probe ran")
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
