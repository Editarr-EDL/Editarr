from pathlib import Path

import pytest
from pydantic import ValidationError

from scanner.settings.model_scan_settings import (
    MODEL_SCAN_SETTINGS_FILENAME,
    ModelScanSettings,
    ModelScanSettingsError,
    load_model_scan_settings,
    save_model_scan_settings,
)


def test_saved_audio_padding_overrides_the_deployment_default(tmp_path: Path) -> None:
    default = load_model_scan_settings(tmp_path, default_audio_padding=0.25)
    assert default.audio_padding_seconds == 0.25

    for value in [0.45, 0]:
        save_model_scan_settings(tmp_path, ModelScanSettings(audio_padding_seconds=value))
        saved = load_model_scan_settings(tmp_path, default_audio_padding=0.25)
        assert saved.audio_padding_seconds == value


@pytest.mark.parametrize("value", [-0.1, False, "0.25", float("nan"), float("inf"), None])
def test_audio_padding_rejects_invalid_values(value) -> None:
    with pytest.raises(ValidationError):
        ModelScanSettings.model_validate({"audio_padding_seconds": value})


def test_invalid_saved_settings_can_be_replaced(tmp_path: Path) -> None:
    (tmp_path / MODEL_SCAN_SETTINGS_FILENAME).write_text('{"audio_padding_seconds": -1}')
    with pytest.raises(ModelScanSettingsError, match="Scan settings are invalid"):
        load_model_scan_settings(tmp_path, default_audio_padding=0.1)

    save_model_scan_settings(tmp_path, ModelScanSettings(audio_padding_seconds=0.2))
    recovered = load_model_scan_settings(tmp_path, default_audio_padding=0.1)
    assert recovered.audio_padding_seconds == 0.2
