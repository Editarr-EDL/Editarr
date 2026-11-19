"""Durable scan settings shared by the Models page and scan workers."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scanner.runtime.durability import atomic_write_bytes, sync_directory
from scanner.runtime.maintenance import DatabaseMaintenance, MaintenanceUnavailable
from scanner.runtime.runtime_paths import ensure_user_private_directory

MODEL_SCAN_SETTINGS_FILENAME = "model-scan-settings.json"


class ModelScanSettingsError(RuntimeError):
    """A safe settings error for callers that share the instance data directory."""


class ModelScanSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio_padding_seconds: float = Field(ge=0, allow_inf_nan=False, strict=True)


def load_model_scan_settings(data_dir: Path, *, default_audio_padding: float) -> ModelScanSettings:
    """Read the saved scan preferences, falling back to the deployment default."""
    try:
        raw = (data_dir / MODEL_SCAN_SETTINGS_FILENAME).read_bytes()
    except FileNotFoundError:
        return ModelScanSettings(audio_padding_seconds=default_audio_padding)
    except OSError as error:
        raise ModelScanSettingsError("Unable to read scan settings") from error
    try:
        return ModelScanSettings.model_validate_json(raw)
    except ValidationError as error:
        raise ModelScanSettingsError("Scan settings are invalid") from error


def save_model_scan_settings(data_dir: Path, settings: ModelScanSettings) -> None:
    """Atomically replace the preference while excluding backup and restore."""
    try:
        with DatabaseMaintenance(data_dir).worker_lease():
            directory = ensure_user_private_directory(data_dir)
            atomic_write_bytes(
                directory / MODEL_SCAN_SETTINGS_FILENAME,
                settings.model_dump_json().encode("utf-8"),
                prefix=".model-scan-settings-",
            )
            try:
                sync_directory(directory)
            except OSError:
                # The replacement succeeded on a filesystem that cannot sync directories.
                pass
    except (OSError, MaintenanceUnavailable) as error:
        raise ModelScanSettingsError("Unable to save scan settings") from error
