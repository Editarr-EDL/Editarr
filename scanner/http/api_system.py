"""Scan request validation and diagnostic settings routes."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.models import ScanName
from scanner.runtime.runtime_logging import set_scanner_log_level
from scanner.security.access.capability_ids import Capability as C
from scanner.security.audit.actions import AuditAction
from scanner.settings.capabilities import (
    DEFAULT_AUDIO_FILTER,
    DEFAULT_AUDIO_MODEL,
    DEFAULT_QWEN_MODEL,
    SUPPORTED_AUDIO_FILTER_SELECTIONS,
    SUPPORTED_AUDIO_MODELS,
    SUPPORTED_QWEN_MODELS,
    SUPPORTED_VIDEO_MODELS,
)
from scanner.settings.config import settings
from scanner.settings.diagnostics import (
    DiagnosticsError,
    DiagnosticsPreferences,
    load_diagnostics_preferences,
    read_log_tail,
    save_diagnostics_preferences,
)
from scanner.settings.version_info import VersionInformation, version_information

from .operations_access import Operator, operation

router = APIRouter()
logger = logging.getLogger("scanner.api")


@router.get(
    "/api/system/version",
    response_model=VersionInformation,
    dependencies=[Depends(operation(C.SYSTEM_VERSION_VIEW))],
)
def api_get_version() -> VersionInformation:
    return version_information()


def _default_video_model() -> str:
    from scanner import api as root

    return root.default_video_model()


class ScanOptions(BaseModel):
    """The options persisted with every queued scan and completed video."""

    name: ScanName = None
    coarse_scan_enabled: bool = False
    coarse_scan_interval: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    # The Severity and Description model; the field keeps its original name.
    qwen_model: str = DEFAULT_QWEN_MODEL
    qwen_summaries_per_segment: int = Field(default=4, ge=1)
    qwen_segment_join_gap_seconds: float = Field(default=3.0, ge=0, allow_inf_nan=False)
    scan_audio: bool = True
    audio_model: str | None = DEFAULT_AUDIO_MODEL
    audio_filter: str | None = DEFAULT_AUDIO_FILTER
    audio_selection: AudioSelection | None = None
    scan_video: bool = True
    # Qwen3-VL 8B where it can run, otherwise NudeNet.
    video_model: str | None = Field(default_factory=lambda: _default_video_model())

    @model_validator(mode="after")
    def validate_scan_options(self):
        """Check model identifiers and scan settings before queue admission."""
        from scanner import api as root

        # A marker-gated test adapter bypasses model availability checks.
        from scanner.testing.test_fake_models import enabled as _test_fake_models_enabled

        fake_models = _test_fake_models_enabled()
        if not self.scan_audio and not self.scan_video:
            raise ValueError("Select Audio scanning, Video scanning, or both")
        if self.scan_audio and self.audio_model not in SUPPORTED_AUDIO_MODELS:
            raise ValueError(f"Unsupported audio model: {self.audio_model}")
        if self.scan_audio and not fake_models:
            root.require_available(self.audio_model)
        if self.scan_audio and self.audio_filter not in SUPPORTED_AUDIO_FILTER_SELECTIONS:
            raise ValueError(f"Unsupported audio filter: {self.audio_filter}")
        if self.scan_audio and not fake_models:
            root.require_available(self.audio_filter)
        if self.scan_video and self.video_model not in SUPPORTED_VIDEO_MODELS:
            raise ValueError(f"Unsupported video model: {self.video_model}")
        if self.qwen_model not in SUPPORTED_QWEN_MODELS:
            raise ValueError(f"Unsupported Severity and Description model: {self.qwen_model}")
        if self.scan_video and not fake_models:
            root.require_available(self.video_model)
            qwen = root.qwen_enrichment_capability()
            if (
                qwen.availability == "unavailable"
                and qwen.enrichment_behavior != "detector_findings_only"
            ):
                root.require_available("qwen3_vl")
        if self.coarse_scan_enabled and not self.scan_video:
            raise ValueError("Coarse scanning requires Video scanning")
        if self.coarse_scan_enabled and (
            self.coarse_scan_interval is None or self.coarse_scan_interval <= 0
        ):
            raise ValueError("Coarse scan interval must be greater than zero")
        if self.scan_video and self.qwen_summaries_per_segment < 1:
            raise ValueError("Representative frames per flagged segment must be at least one")
        if self.scan_video and self.qwen_segment_join_gap_seconds < 0:
            raise ValueError("Qwen segment join gap cannot be negative")
        return self


class ScanRequest(ScanOptions):
    """Queue a scan of one Jellyfin item.

    Audio scans also need an `audio_selection` from GET /api/media/audio-tracks;
    the published example is video-only, which needs none.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"jellyfin_item_id": "JELLYFIN_ITEM_ID", "scan_audio": False, "scan_video": True}
            ]
        }
    )

    jellyfin_item_id: str
    media_source_id: str | None = None


class PathResolutionRequest(BaseModel):
    remote_path: str


class DiagnosticLogSettings(BaseModel):
    debug_enabled: bool = False


class DiagnosticLogTail(BaseModel):
    channel: Literal["application", "controller", "http", "worker"]
    lines: list[str]
    truncated: bool


@router.get(
    "/api/system/logs/settings",
    response_model=DiagnosticLogSettings,
    dependencies=[Depends(operation(C.LOGS_VIEW))],
)
def api_get_diagnostic_log_settings():
    try:
        preferences = load_diagnostics_preferences(settings.data_dir)
    except DiagnosticsError as exc:
        raise HTTPException(503, str(exc)) from exc
    return DiagnosticLogSettings(debug_enabled=preferences.debug_enabled)


@router.put("/api/system/logs/settings", response_model=DiagnosticLogSettings)
def api_update_diagnostic_log_settings(
    update: DiagnosticLogSettings,
    caller: Operator = Depends(operation(C.LOGS_SETTINGS_EDIT, AuditAction.LOGGING_CHANGE)),
):
    preferences = DiagnosticsPreferences(debug_enabled=update.debug_enabled)
    try:
        save_diagnostics_preferences(settings.data_dir, preferences)
    except DiagnosticsError as exc:
        raise HTTPException(503, str(exc)) from exc
    set_scanner_log_level(preferences.debug_enabled)
    logger.info("Debug logging %s", "enabled" if preferences.debug_enabled else "disabled")
    caller.record(AuditAction.LOGGING_CHANGE, {"debug_enabled": preferences.debug_enabled})
    return update


@router.get(
    "/api/system/logs/{channel}",
    response_model=DiagnosticLogTail,
    dependencies=[Depends(operation(C.LOGS_VIEW, paths=True))],
)
def api_get_diagnostic_log_tail(channel: str, limit: int = 300):
    try:
        lines, truncated = read_log_tail(settings.log_dir, channel, limit=limit)
    except DiagnosticsError as exc:
        raise HTTPException(400, str(exc)) from exc
    logger.debug("Read %d diagnostic lines from %s", len(lines), channel)
    return DiagnosticLogTail(channel=channel, lines=lines, truncated=truncated)
