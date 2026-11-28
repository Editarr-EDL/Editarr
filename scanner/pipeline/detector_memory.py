"""Keep one large vision model resident during a visual scan.

Large detectors (d1-3B, the Qwen detectors, and Shieldstral) stay loaded for the
whole detection pass. When the Severity and Description model is a different artifact,
holding both would roughly double peak memory (about 18 GB for d1-3B plus Qwen3-VL 8B).
Such scans therefore detect every scene first, release the detector, and then run the
deferred descriptions; a Qwen detector that is the same artifact as the description
model keeps its single shared instance.
"""

from __future__ import annotations

import gc
import logging
import sys

from .qwen_detector import ARTIFACTS as QWEN_DETECTOR_ARTIFACTS

logger = logging.getLogger(__name__)
_RESIDENT_DETECTORS = ("d1-3b", "shieldstral", *QWEN_DETECTOR_ARTIFACTS)


def defer_descriptions(video_model: str | None, qwen_model: str) -> bool:
    """Whether descriptions must wait until the detector has been released."""
    if video_model not in _RESIDENT_DETECTORS:
        return False
    return QWEN_DETECTOR_ARTIFACTS.get(video_model) != qwen_model


def _clear_mlx_cache() -> None:
    mx = sys.modules.get("mlx.core")
    if mx is not None:
        mx.clear_cache()


def release_detector(video_model: str | None) -> None:
    """Drop a resident detector's weights and return its accelerator memory.

    Only modules that were imported (and so may hold a model) are touched; nothing is
    imported or loaded here.
    """
    released = False
    if video_model == "d1-3b" and "scanner.pipeline.d1_detector" in sys.modules:
        released = sys.modules["scanner.pipeline.d1_detector"].release()
    elif video_model == "shieldstral" and "scanner.pipeline.shieldstral" in sys.modules:
        engines = sys.modules["scanner.pipeline.shieldstral"]._CACHE
        released = engines.pop("engine", None) is not None
    elif video_model in QWEN_DETECTOR_ARTIFACTS and "scanner.pipeline.qwen_summary" in sys.modules:
        cache = sys.modules["scanner.pipeline.qwen_summary"]._CACHE
        released = cache.pop(QWEN_DETECTOR_ARTIFACTS[video_model], None) is not None
    if not released:
        return
    gc.collect()
    _clear_mlx_cache()
    logger.info("Released the %s detector before the description step", video_model)
