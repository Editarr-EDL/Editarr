"""Pipeline orchestrator — runs visual and audio scans on a video."""

import json
import logging
import multiprocessing as mp  # noqa: F401 - compatibility patch point
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from scanner.models import FindingCreate
from scanner.pipeline.cache import (
    atomic_write_json as atomic_write_json,
)
from scanner.pipeline.cache import (
    cached_audio_path as cached_audio_path,
)
from scanner.pipeline.cache import (
    transcription_cache_path as transcription_cache_path,
)
from scanner.pipeline.frames import (
    get_video_duration,
)
from scanner.pipeline.frames import (
    get_video_frame_interval as get_video_frame_interval,
)
from scanner.pipeline.processes import ScanCancelledError
from scanner.pipeline.provenance import effective_stage_settings
from scanner.pipeline.runner_audio import run_audio_stages
from scanner.pipeline.runner_scenes import _scan_visual_by_scene as _scan_visual_by_scene
from scanner.pipeline.runner_workers import (
    _CANCELLATION_GRACE_SECONDS as _CANCELLATION_GRACE_SECONDS,
)
from scanner.pipeline.runner_workers import (
    _scan_audio_isolated as _scan_audio_isolated,
)
from scanner.pipeline.runner_workers import (
    _scan_audio_worker as _scan_audio_worker,
)
from scanner.pipeline.runner_workers import (
    _scan_visual_isolated as _scan_visual_isolated,
)
from scanner.pipeline.runner_workers import (
    _scan_visual_worker as _scan_visual_worker,
)
from scanner.pipeline.runner_workers import (
    _terminate_worker as _terminate_worker,
)
from scanner.pipeline.scenes import detect_scenes as detect_scenes
from scanner.pipeline.scenes import sample_scene_frames as sample_scene_frames
from scanner.pipeline.visual_checkpoints import discard_visual_checkpoints, visual_checkpoint_dir
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.runtime.process_control import terminate_isolated_process as terminate_isolated_process
from scanner.runtime.runtime_workspaces import (
    cleanup_stale_scan_workspaces,
    create_scan_workspace,
    remove_scan_workspace,
)
from scanner.runtime.worker_quit import FullApplicationQuitRequested
from scanner.settings.capabilities import (
    DEFAULT_QWEN_MODEL,
    SUPPORTED_QWEN_MODELS,
    CapabilityUnavailableError,
    qwen_enrichment_capability,
    require_available,
)
from scanner.settings.config import settings
from scanner.settings.model_scan_settings import load_model_scan_settings
from scanner.testing.test_fake_models import enabled as _test_fake_models_enabled

logger = logging.getLogger(__name__)


def _load_cached_visual_findings(path: Path) -> list[FindingCreate] | None:
    try:
        with path.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if not isinstance(payload, list):
            return None
        return [FindingCreate(**item) for item in payload]
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


STAGE_WEIGHTS = {
    "setup": 0.05,
    "visual": 0.60,
    "audio_extract": 0.10,
    "audio_scan": 0.20,
    "cleanup": 0.0,
    "finalize": 0.05,
}

STAGE_ORDER = ["setup", "visual", "audio_extract", "audio_scan", "cleanup", "finalize"]


def _run_test_fake_models(
    db: Session,
    video_id: str,
    *,
    progress_callback: Callable[[dict[str, object]], None] | None,
    should_cancel: Callable[[], bool] | None,
    status_updater: Callable[[Session, str, str], None],
    findings_writer: Callable[[Session, str, list[FindingCreate]], None],
) -> str:
    """Exercise worker lifecycle only; this never loads or represents inference."""
    status_updater(db, video_id, "scanning")
    # Leave enough time for the installed-artifact verifier to observe the
    # dedicated child and issue a cooperative cancellation.  This remains a
    # deterministic adapter only; it never touches a model backend or network.
    for step in range(120):
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        if progress_callback:
            progress_callback(
                {
                    "video_id": video_id,
                    "status": "scanning",
                    "stage": "test_fake_models",
                    "stage_fraction": step / 120,
                    "percent": round((step / 120) * 100, 2),
                    "message": "Test-only fake model adapter is running",
                }
            )
        time.sleep(0.1)
    findings_writer(
        db,
        video_id,
        [
            FindingCreate(
                video_id=video_id,
                category="violence",
                subcategory="violence",
                start_seconds=0,
                end_seconds=1,
                severity="medium",
                confidence=1.0,
                label="Test-only fake model finding",
                source="test_fixture",
            )
        ],
    )
    status_updater(db, video_id, "complete")
    return video_id


def _audio_pipeline_module():
    from scanner.pipeline import audio as audio_pipeline

    return audio_pipeline


def _visual_pipeline_module():
    from scanner.pipeline import visual as visual_pipeline

    return visual_pipeline


def scan_audio(*args, **kwargs):
    return _audio_pipeline_module().scan_audio(*args, **kwargs)


def extract_audio(*args, **kwargs):
    return _audio_pipeline_module().extract_audio(*args, **kwargs)


def transcription_cache_model_key(*args, **kwargs):
    return _audio_pipeline_module().transcription_cache_model_key(*args, **kwargs)


def scan_frame(*args, **kwargs):
    return _visual_pipeline_module().scan_frame(*args, **kwargs)


def scan_frames(*args, **kwargs):
    return _visual_pipeline_module().scan_frames(*args, **kwargs)


def summarize_flagged_segments(*args, **kwargs):
    return _visual_pipeline_module().summarize_flagged_segments(*args, **kwargs)


def _stage_percent(
    stage: str,
    stage_fraction: float,
    *,
    scan_audio: bool,
    scan_video: bool,
) -> float:
    """Convert a stage fraction into overall progress using enabled scan weights."""
    stage_fraction = max(0.0, min(stage_fraction, 1.0))
    active_weights = {
        name: weight
        for name, weight in STAGE_WEIGHTS.items()
        if name in {"setup", "cleanup", "finalize"}
        or (name == "visual" and scan_video)
        or (name in {"audio_extract", "audio_scan"} and scan_audio)
    }
    total_weight = sum(active_weights.values())
    completed = 0.0
    for name in STAGE_ORDER:
        weight = active_weights.get(name, 0.0)
        if name == stage:
            return ((completed + (weight * stage_fraction)) / total_weight) * 100.0
        completed += weight
    return 100.0


def run_scan(
    db: Session,
    video_path: str,
    coarse_scan_enabled: bool = False,
    coarse_scan_interval_seconds: float | None = None,
    qwen_model: str = DEFAULT_QWEN_MODEL,
    qwen_summaries_per_segment: int = 4,
    qwen_segment_join_gap_seconds: float = 3.0,
    scan_audio: bool = True,
    audio_model: str | None = None,
    audio_filter: str | None = None,
    scan_video: bool = True,
    video_model: str | None = None,
    video_id: str | None = None,
    video_duration_seconds: float | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    status_updater: Callable[[Session, str, str], None] | None = None,
    findings_writer: Callable[[Session, str, list[FindingCreate]], None] | None = None,
    scan_metadata_updater: Callable[[Session, str, float], None] | None = None,
    completed_stages: set[str] | None = None,
    checkpoint_callback: Callable[[str], None] | None = None,
    stage_callback: Callable[[str, str, dict[str, object]], None] | None = None,
    *,
    audio_selection=None,
    analyzed_audio_callback=None,
    analyzed_audio_timing_callback=None,
    media_cache_key: str | None = None,
    stage_results_writer: (
        Callable[[str, list[FindingCreate], dict[str, object]], None] | None
    ) = None,
    checkpoint_worker_token: str | None = None,
) -> str:
    """
    Run the full analysis pipeline on a video file.

    Returns the Editarr scan record ID.
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if video_id is None or status_updater is None or findings_writer is None:
        raise ValueError("Editarr scans require a scan record and result writers")
    completed_stages = completed_stages or set()
    if scan_audio and audio_selection is not None:
        from .audio_tracks import probe_audio_tracks

        probe_audio_tracks(path).revalidate(audio_selection)

    # Reject invalid options before reading media, changing scan state, or allocating
    # an owned workspace.  A caller can retry with corrected options without leaving
    # disposable frame directories behind.
    if not scan_audio and not scan_video:
        raise ValueError("At least one scan type must be enabled")
    if coarse_scan_enabled and not scan_video:
        raise ValueError("Coarse scanning requires visual scanning")
    if coarse_scan_enabled and (
        coarse_scan_interval_seconds is None or coarse_scan_interval_seconds <= 0
    ):
        raise ValueError("Coarse scan interval must be greater than zero")
    if scan_video and settings.scene_detection_threshold <= 0:
        raise ValueError("Scene detection threshold must be greater than zero")
    if qwen_summaries_per_segment < 1:
        raise ValueError("Representative frames per flagged segment must be at least one")
    if qwen_segment_join_gap_seconds < 0:
        raise ValueError("Qwen segment join gap cannot be negative")
    if qwen_model not in SUPPORTED_QWEN_MODELS:
        raise ValueError(f"Unsupported Severity and Description model: {qwen_model}")

    audio_padding = settings.profanity_pad_seconds
    if scan_audio and "audio_scan" not in completed_stages:
        audio_padding = load_model_scan_settings(
            settings.data_dir, default_audio_padding=audio_padding
        ).audio_padding_seconds
    stage_settings = effective_stage_settings(
        video_model=video_model,
        audio_model=audio_model,
        audio_filter=audio_filter,
        audio_padding_seconds=audio_padding,
        qwen_model=qwen_model,
        scene_detection_threshold=settings.scene_detection_threshold,
        coarse_scan_enabled=coarse_scan_enabled,
        coarse_scan_interval_seconds=coarse_scan_interval_seconds,
        qwen_summaries_per_segment=qwen_summaries_per_segment,
        qwen_segment_join_gap_seconds=qwen_segment_join_gap_seconds,
    )
    if scan_audio and audio_selection is not None:
        from dataclasses import asdict

        for stage in ("audio_extract", "audio_scan", "audio_qwen"):
            stage_settings[stage]["audio_selection"] = asdict(audio_selection)

    def record_stage(stage: str, state: str, **observed: object) -> None:
        effective = stage_settings[stage]
        effective.update(observed)
        if stage_callback:
            stage_callback(stage, state, effective)

    def record_visual_enrichment(event: str) -> None:
        scene, separator, state = event.partition(":")
        if not separator or not scene.startswith("scene_"):
            raise ValueError("Visual enrichment event lacks a scene identity")
        stage = f"visual_qwen:{scene}"
        if state == "reused":
            stage_settings["visual"]["cached_scene_findings_reused"] = True
        elif state == "checkpoint_reused":
            # This scene already ran for the same job, media and model settings.
            # Keep combined detector coverage without claiming a new Qwen run.
            stage_settings["visual"]["checkpoint_scene_findings_reused"] = True
            state = "reused"
        stage_settings[stage] = stage_settings["visual_qwen"]
        record_stage(stage, state)

    def complete_stage(stage: str, findings: list[FindingCreate]) -> None:
        if stage_results_writer is not None:
            stage_results_writer(stage, findings, stage_settings[stage].copy())
            return
        if findings:
            findings_writer(db, video_id, findings)
        if checkpoint_callback:
            checkpoint_callback(stage)
        record_stage(stage, "complete")

    if _test_fake_models_enabled():
        return _run_test_fake_models(
            db,
            video_id,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            status_updater=status_updater,
            findings_writer=findings_writer,
        )
    if scan_audio and "audio_scan" not in completed_stages:
        try:
            require_available(audio_model or "whisper")
            require_available(audio_filter or "better_profanity")
        except CapabilityUnavailableError:
            record_stage("audio_scan", "unsupported")
            raise
    if scan_video and "visual" not in completed_stages:
        try:
            require_available(video_model or "nudenet")
        except CapabilityUnavailableError:
            record_stage("visual", "unsupported")
            raise
        qwen = qwen_enrichment_capability()
        stage_settings["visual"]["qwen_availability"] = qwen.availability
        stage_settings["visual_qwen"]["qwen_availability"] = qwen.availability
        if (
            qwen.availability == "unavailable"
            and qwen.enrichment_behavior != "detector_findings_only"
        ):
            try:
                require_available("qwen3_vl")
            except CapabilityUnavailableError:
                record_stage("visual", "unsupported")
                raise
        record_stage(
            "visual_qwen",
            "unsupported" if qwen.availability == "unavailable" else "requested",
        )

    def emit(stage: str, stage_fraction: float, message: str, status: str = "scanning") -> None:
        """Send one bounded progress update for the current scan stage."""
        if not progress_callback:
            return
        progress_callback(
            {
                "video_id": video_id,
                "status": status,
                "stage": stage,
                "stage_fraction": max(0.0, min(stage_fraction, 1.0)),
                "percent": round(
                    _stage_percent(
                        stage,
                        stage_fraction,
                        scan_audio=scan_audio,
                        scan_video=scan_video,
                    ),
                    2,
                ),
                "message": message,
            }
        )

    if video_duration_seconds is None or video_duration_seconds <= 0:
        emit("setup", 0.0, "Reading media metadata")
        duration = get_video_duration(str(path))
    else:
        duration = video_duration_seconds
    if scan_metadata_updater:
        scan_metadata_updater(db, video_id, duration)
    for effective in stage_settings.values():
        effective["duration_seconds"] = duration

    status_updater(db, video_id, "scanning")
    logger.info("Scanning %s (%.1fs)", path.name, duration)

    emit("setup", 0.7, "Initialized scan")

    video_hash = media_cache_key
    if video_hash is None:
        emit("setup", 0.8, "Computing video hash")
        video_hash = sampled_media_fingerprint(path).artifact_cache_key
    logger.info("Using video content hash for cache key: %s", video_hash)
    emit("setup", 1.0, "Video hash computed")

    if should_cancel and should_cancel():
        raise ScanCancelledError("Scan cancelled")

    cleanup_stale_scan_workspaces(settings.frames_dir)
    work_dir = create_scan_workspace(settings.frames_dir, video_id)
    cancelled = False
    try:
        if scan_video and "visual" not in completed_stages:
            record_stage("visual", "started")
            checkpoint_dir = visual_checkpoint_dir(
                video_id, video_hash, stage_settings["visual"], stage_settings["visual_qwen"]
            ) if stage_results_writer is not None else None
            # The visual classifier is loaded after positive detections. Keep
            # this pass isolated so its model memory is released before audio.
            try:
                visual_findings = _scan_visual_isolated(
                    str(path),
                    video_id,
                    work_dir,
                    coarse_scan_enabled=coarse_scan_enabled,
                    coarse_scan_interval_seconds=coarse_scan_interval_seconds,
                    qwen_model=qwen_model,
                    qwen_summaries_per_segment=qwen_summaries_per_segment,
                    qwen_segment_join_gap_seconds=qwen_segment_join_gap_seconds,
                    video_duration_seconds=duration,
                    video_hash=video_hash,
                    video_model=video_model,
                    progress_callback=lambda frac, msg: emit("visual", frac, msg),
                    provenance_callback=lambda event: record_visual_enrichment(event),
                    should_cancel=should_cancel,
                    **({"checkpoint_dir": checkpoint_dir} if checkpoint_dir is not None else {}),
                    **({"checkpoint_worker_token": checkpoint_worker_token}
                       if checkpoint_worker_token is not None else {}),
                )
                emit("visual", 1.0, "Visual scan complete")
                complete_stage("visual", visual_findings)
            except BaseException as exc:
                record_stage(
                    "visual", "partial" if isinstance(
                        exc, (ScanCancelledError, FullApplicationQuitRequested)
                    ) else "failed"
                )
                raise
            if checkpoint_dir is not None:
                discard_visual_checkpoints(settings.data_dir, video_id)
        elif scan_video:
            emit("visual", 1.0, "Visual scan restored from checkpoint")

        if scan_audio:
            run_audio_stages(
                path,
                video_hash=video_hash,
                duration=duration,
                video_id=video_id,
                audio_model=audio_model,
                audio_filter=audio_filter,
                audio_padding_seconds=audio_padding,
                qwen_model=qwen_model,
                audio_selection=audio_selection,
                should_cancel=should_cancel,
                completed_stages=completed_stages,
                complete_stage=complete_stage,
                analyzed_audio_callback=analyzed_audio_callback,
                analyzed_audio_timing_callback=analyzed_audio_timing_callback,
                record_stage=record_stage,
                emit=emit,
            )

        emit("finalize", 0.5, "Saving findings")
        status_updater(db, video_id, "complete")
        emit("finalize", 1.0, "Scan complete", status="complete")
        logger.info("Scan complete")

    except ScanCancelledError:
        cancelled = True
        raise
    except FullApplicationQuitRequested:
        # A Quit pauses the scan for a later resume; the worker settles its status.
        raise
    except Exception:
        status_updater(db, video_id, "error")
        emit("finalize", 1.0, "Scan failed", status="error")
        raise
    finally:
        if work_dir.exists():
            if cancelled:
                emit("cleanup", 0.0, "Cleaning up extracted files", status="cancelling")
            remove_scan_workspace(work_dir)

    return video_id
