"""Offline model identity for executed scanner stages."""

from importlib.metadata import PackageNotFoundError, version

from scanner.modeling.model_acquisition_manifest import artifact_ids_for_backend, manifest


def effective_stage_settings(
    *,
    video_model: str | None,
    audio_model: str | None,
    audio_filter: str | None,
    qwen_model: str,
    scene_detection_threshold: float,
    coarse_scan_enabled: bool,
    coarse_scan_interval_seconds: float | None,
    qwen_summaries_per_segment: int,
    qwen_segment_join_gap_seconds: float,
) -> dict[str, dict[str, object]]:
    """Describe arguments actually passed to the audio and visual workers."""
    return {
        "visual": {
            "video_model": video_model or "nudenet",
            "scene_detection_threshold": scene_detection_threshold,
            "coarse_scan_enabled": coarse_scan_enabled,
            "coarse_scan_interval_seconds": coarse_scan_interval_seconds,
            "qwen_model_requested": qwen_model,
            "qwen_summaries_per_segment": qwen_summaries_per_segment,
            "qwen_segment_join_gap_seconds": qwen_segment_join_gap_seconds,
        },
        "audio_extract": {"source": "video", "output": "cached_audio"},
        "audio_scan": {
            "audio_model": audio_model or "whisper",
            "audio_filter": audio_filter or "better_profanity",
            "qwen_model_requested": qwen_model,
        },
        "visual_qwen": {"qwen_model_requested": qwen_model},
        "audio_qwen": {"qwen_model_requested": qwen_model},
    }


_PACKAGE_BACKENDS = {
    "nudenet": "nudenet",
    "better_profanity": "better-profanity",
    "glin_profanity": "glin-profanity",
    "alt_profanity_check": "alt-profanity-check",
}


def stage_model_identity(stage: str, settings: dict[str, object]) -> dict[str, str | None]:
    """Resolve selected pinned model identity without loading or acquiring weights."""
    backend = {
        "visual": settings.get("video_model"),
        "audio_scan": settings.get("audio_model"),
        "visual_qwen": settings.get("qwen_model_requested"),
        "audio_qwen": settings.get("qwen_model_requested"),
    }.get(stage)
    if stage.startswith("visual_qwen:scene_"):
        backend = settings.get("qwen_model_requested")
    if stage == "credits_detection":
        backend = settings.get("detector_version")
    if stage == "credits_qwen":
        backend = settings.get("qwen_model")
    if not isinstance(backend, str):
        return {"model_id": None, "model_revision": None, "model_digest": None,
                "quantization": None}
    artifact_ids = (
        (backend,) if backend.startswith("qwen3-vl-")
        else () if stage == "credits_detection" else artifact_ids_for_backend(backend)
    )
    artifacts = [manifest().artifact(artifact_id) for artifact_id in artifact_ids]
    revisions = {artifact.revision for artifact in artifacts}
    revision = next(iter(revisions)) if len(revisions) == 1 else None
    digest = (
        artifacts[0].files[0].sha256
        if len(artifacts) == 1 and len(artifacts[0].files) == 1
        else None
    )
    if not artifacts and backend in _PACKAGE_BACKENDS:
        try:
            revision = version(_PACKAGE_BACKENDS[backend])
        except PackageNotFoundError:
            pass
    return {
        "model_id": backend,
        "model_revision": revision,
        "model_digest": digest,
        "quantization": "4bit" if backend.endswith("-4bit") else None,
    }
