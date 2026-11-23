"""Queued execution for closing-credits detection scans."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

from pydantic import ValidationError
from sqlalchemy.orm import Session

from scanner.models import CreditsDetectionConfiguration, CreditsDetectionResult
from scanner.pipeline.cache import (
    compute_video_md5,
    load_cached_credits_result,
    save_cached_credits_result,
)
from scanner.pipeline.frames import get_video_duration
from scanner.pipeline.processes import ScanCancelledError
from scanner.repository import complete_credits_detection, update_scan_duration
from scanner.runtime.runtime_workspaces import create_scan_workspace, remove_scan_workspace
from scanner.settings.config import settings

from .credits_detector import credits_cache_key
from .credits_media import assess_frames_with_qwen, detect_credits_in_media

logger = logging.getLogger(__name__)


def _progress(
    callback: Callable[[dict[str, object]], None] | None,
    percent: float,
    stage: str,
    message: str,
) -> None:
    if callback is not None:
        callback({"percent": percent, "stage": stage, "message": message})


def run_credits_scan(
    db: Session,
    video_path: str,
    *,
    scan_id: str,
    configuration: CreditsDetectionConfiguration,
    video_duration_seconds: float | None,
    video_hash: str | None,
    status_updater: Callable[[Session, str, str], None],
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    stage_callback: Callable[[str, str, dict[str, object]], None] | None = None,
    result_writer: Callable = complete_credits_detection,
    scan_metadata_updater: Callable = update_scan_duration,
    media_cache_key: str | None = None,
) -> CreditsDetectionResult:
    """Run one detector configuration and atomically persist its evidence."""
    status_updater(db, scan_id, "scanning")
    _progress(progress_callback, 5.0, "setup", "Preparing credits detection")
    if should_cancel and should_cancel():
        raise ScanCancelledError("Scan cancelled")

    duration = video_duration_seconds
    if duration is None or not math.isfinite(duration) or duration <= 0:
        duration = get_video_duration(video_path)
        scan_metadata_updater(db, scan_id, duration)
    resolved_hash = media_cache_key or compute_video_md5(video_path)
    if should_cancel and should_cancel():
        raise ScanCancelledError("Scan cancelled")
    if media_cache_key is None and video_hash is not None and resolved_hash != video_hash:
        raise ValueError("Credits detection media changed after the scan was queued")
    cache_key = credits_cache_key(
        video_hash=resolved_hash,
        configuration=configuration,
    )
    cached_payload = load_cached_credits_result(cache_key)
    effective = {
        **configuration.model_dump(mode="json"),
        "duration_seconds": duration,
    }

    def record(stage: str, state: str, *, cache_hit: bool = False) -> None:
        if stage_callback:
            stage_callback(stage, state, {**effective, "cache_hit": cache_hit})

    if cached_payload is not None:
        try:
            result = CreditsDetectionResult.model_validate(cached_payload)
        except ValidationError:
            result = None
        if result is not None:
            if should_cancel and should_cancel():
                raise ScanCancelledError("Scan cancelled")
            _progress(progress_callback, 90.0, "credits_cache", "Using cached credits result")
            record("credits_cache", "started", cache_hit=True)
            result_writer(
                db,
                scan_id=scan_id,
                cache_key=cache_key,
                result=result,
            )
            record("credits_cache", "complete", cache_hit=True)
            return result

    workspace = create_scan_workspace(settings.frames_dir, scan_id)
    record("credits_detection", "started")
    try:
        _progress(progress_callback, 15.0, "credits_analysis", "Analyzing video tail")
        qwen_assessor = None
        if configuration.qwen_model is not None:

            def qwen_assessor(paths, timestamps, _model):
                record("credits_qwen", "started")
                try:
                    assessment = assess_frames_with_qwen(
                        paths,
                        timestamps,
                        qwen_model=configuration.qwen_model,
                    )
                except BaseException:
                    record("credits_qwen", "failed")
                    raise
                record("credits_qwen", "complete")
                return assessment

        result = detect_credits_in_media(
            video_path,
            duration_seconds=duration,
            configuration=configuration,
            workspace=workspace,
            qwen_assessor=qwen_assessor,
            should_cancel=should_cancel,
        )
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        try:
            save_cached_credits_result(cache_key, result.model_dump(mode="json"))
        except OSError:
            logger.warning("Unable to cache credits result for scan %s", scan_id, exc_info=True)
        _progress(progress_callback, 90.0, "credits_finalize", "Saving credits result")
        result_writer(
            db,
            scan_id=scan_id,
            cache_key=cache_key,
            result=result,
        )
        record("credits_detection", "complete")
        return result
    except BaseException as exc:
        record(
            "credits_detection",
            "partial" if isinstance(exc, ScanCancelledError) else "failed",
        )
        raise
    finally:
        remove_scan_workspace(workspace)
