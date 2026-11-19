"""Shieldstral 1.0 3B frame screening, family routing, and visual detections.

The MLX INT8 build runs only on Apple silicon. Each frame is screened with the
policies in :mod:`scanner.pipeline.shieldstral_policies`; sexual-positive frames are
then routed to nudity and/or sex families. The model (about 5.3 GB peak at the
288 px default) is loaded lazily and cached for the lifetime of the scan worker
process, alongside any Qwen3-VL summary model.
"""

from __future__ import annotations

import platform
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Lock

from scanner.evaluation.subcategorization import subcategorize_detector_label
from scanner.model_acquisition import acquire_artifact
from scanner.settings.capabilities import require_available
from scanner.settings.config import settings

from .dependencies import require_dependency
from .shieldstral_inference import ShieldstralEngine
from .shieldstral_policies import (
    LABEL_FAMILIES,
    NUDITY_LABEL,
    SCREENS,
    SEXUAL_LABEL,
    SEXUAL_ROUTES,
    SEXUAL_SCREEN,
    fired_route_labels,
    positive_screens,
    route_sexual_labels,
)
from .stride_sampling import sample_and_hold
from .visual import VisualDetection, _clamp_confidence, _confidence_to_severity

ARTIFACT_ID = "shieldstral-3b-int8"
MODEL_ID = "sahilchachra/Shieldstral-1.0-3B-INT8"
MODEL_REVISION = "accac83025f1164a388e0d704840809a4d46bffd"
_CACHE: dict[str, ShieldstralEngine] = {}
_LOAD_LOCK = Lock()


def _require_dependency(import_name: str, install_hint: str):
    return require_dependency(import_name, install_hint, "Shieldstral requires")


def _load_engine() -> ShieldstralEngine:
    """Acquire the pinned artifact and cache one scoring engine per process."""
    require_available("shieldstral")
    if "engine" in _CACHE:
        return _CACHE["engine"]
    with _LOAD_LOCK:
        if "engine" in _CACHE:
            return _CACHE["engine"]
        if platform.system() != "Darwin":
            raise RuntimeError("Shieldstral uses MLX and requires a Mac running macOS.")
        mlx_vlm = _require_dependency("mlx_vlm", "mlx-vlm")
        mx = _require_dependency("mlx.core", "mlx")
        numpy = _require_dependency("numpy", "numpy")
        try:
            kv_cache = _require_dependency("mlx_vlm.models.cache", "mlx-vlm").KVCache
        except (AttributeError, RuntimeError):
            kv_cache = None
        model_dir = acquire_artifact(ARTIFACT_ID)
        model, processor = mlx_vlm.load(str(model_dir))
        template = (model_dir / "chat_template.jinja").read_text(encoding="utf-8")
        _CACHE["engine"] = ShieldstralEngine(
            model, processor, template, mx=mx, numpy=numpy, kv_cache=kv_cache
        )
        return _CACHE["engine"]


def load_frame(frame_path: Path, max_height: int | None = None):
    """Open a frame as RGB, downscaling to ``max_height`` with an even width (bicubic).

    This matches the evaluated ``scale=-2:min(h,ih)`` frame preparation.
    """
    image_module = _require_dependency("PIL.Image", "pillow")
    limit = settings.shieldstral_max_image_height if max_height is None else max_height
    with image_module.open(frame_path) as opened:
        image = opened.convert("RGB")
    if limit and image.height > limit:
        width = max(2, round(image.width * limit / image.height / 2) * 2)
        image = image.resize((width, limit), image_module.Resampling.BICUBIC)
    return image


@dataclass(frozen=True)
class FrameResult:
    """Screen scores for one frame plus routing scores when its sexual screen fired."""

    screens: dict[str, float]
    routes: dict[str, float] = field(default_factory=dict)
    # Label used when the sexual screen fired but no route did (family unknown).
    fallback_label: str = SEXUAL_LABEL

    @property
    def sexual_positive(self) -> bool:
        return SEXUAL_SCREEN in positive_screens(self.screens)

    @property
    def uses_fallback(self) -> bool:
        return self.sexual_positive and not fired_route_labels(self.routes)

    @property
    def routed_to_nudity(self) -> bool:
        return self.sexual_positive and NUDITY_LABEL in fired_route_labels(self.routes)

    def labels(self) -> dict[str, float]:
        """Map each emitted detection label to its confidence (the screen score)."""
        emitted: dict[str, float] = {}
        for screen in positive_screens(self.screens):
            confidence = self.screens[screen.id]
            if screen is SEXUAL_SCREEN:
                for label in route_sexual_labels(self.routes, fallback=self.fallback_label):
                    emitted[label] = confidence
            else:
                emitted[screen.label] = confidence
        return emitted

    def key(self) -> frozenset[str]:
        """Labels used to detect boundaries during fine-pass sample-and-hold.

        Unknown-family frames get a distinct marker so their edges are refined too;
        run smoothing may later report them under a different family.
        """
        marker = {"unknown_sexual_family"} if self.uses_fallback else set()
        return frozenset(self.labels()) | marker


def smooth_sexual_runs(results: Iterable[FrameResult]) -> Iterator[FrameResult]:
    """Resolve unknown-family frames from their contiguous sexual-positive run.

    Within each maximal run of sexual-positive frames that contains a nudity-routed
    frame, frames where no route fired are reported as nudity instead of suggestive
    sex. Frames that fired a route, runs without nudity, and all other frames pass
    through unchanged. Results are buffered only while inside a run, in order.
    """
    run: list[FrameResult] = []

    def flush() -> list[FrameResult]:
        if any(result.routed_to_nudity for result in run):
            return [
                replace(result, fallback_label=NUDITY_LABEL) if result.uses_fallback else result
                for result in run
            ]
        return list(run)

    for result in results:
        if result.sexual_positive:
            run.append(result)
            continue
        yield from flush()
        run.clear()
        yield result
    yield from flush()


def evaluate_frame(score: Callable[[str], float]) -> FrameResult:
    """Run every screen, then the sexual routes only if the sexual screen fired."""
    screens = {screen.id: score(screen.query) for screen in SCREENS}
    if SEXUAL_SCREEN not in positive_screens(screens):
        return FrameResult(screens)
    return FrameResult(screens, {route.id: score(route.query) for route in SEXUAL_ROUTES})


def score_frame(frame_path: Path) -> FrameResult:
    """Load, downscale, screen, and route one extracted frame."""
    return evaluate_frame(_load_engine().frame(load_frame(frame_path)).score)


def detections_for_result(result: FrameResult, timestamp: float) -> list[VisualDetection]:
    """Emit one detection per emitted label, with the screen score as confidence."""
    detections: list[VisualDetection] = []
    for label, score in result.labels().items():
        confidence = _clamp_confidence(score)
        classification = subcategorize_detector_label(
            producer="shieldstral",
            category=LABEL_FAMILIES[label],
            label=label,
            confidence=confidence,
            model_id=MODEL_ID,
            model_revision=MODEL_REVISION,
        )
        detections.append(
            VisualDetection(
                timestamp=timestamp,
                label=label,
                confidence=confidence,
                category=classification.category,
                subcategory=classification.subcategory.value,
                severity=_confidence_to_severity(confidence),
            )
        )
    return detections


def scan_frame(frame_path: Path, timestamp: float) -> list[VisualDetection]:
    """Score one coarse sample and return its detections."""
    return detections_for_result(score_frame(frame_path), timestamp)


def fine_stride(frame_interval_seconds: float) -> int:
    """Return how many source frames separate strided fine-pass samples."""
    if frame_interval_seconds <= 0:
        return 1
    return max(1, round(settings.shieldstral_fine_sample_interval_seconds / frame_interval_seconds))


def scan_frames(
    frames: list[tuple[Path, float]], frame_interval_seconds: float
) -> Iterator[list[VisualDetection]]:
    """Yield one detection list per frame, scoring a strided subset with refined edges.

    Boundaries are refined whenever the emitted label set changes, including a change
    of routed family inside a sexual run. Unscored frames reuse the nearest scored
    result within the same uniform run, stamped with their own timestamp. Unknown-family
    sexual frames are then resolved by :func:`smooth_sexual_runs`.
    """
    results = sample_and_hold(
        len(frames),
        fine_stride(frame_interval_seconds),
        lambda index: score_frame(frames[index][0]),
        FrameResult.key,
    )
    for (_frame_path, timestamp), result in zip(
        frames, smooth_sexual_runs(results), strict=True
    ):
        yield detections_for_result(result, timestamp)

