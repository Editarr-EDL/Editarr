"""Apple Speech capability and the timed transcript pipeline."""

import json
import wave
from pathlib import Path

import pytest

from scanner.pipeline import apple_speech, audio
from scanner.settings import capabilities


@pytest.mark.parametrize(
    ("system", "machine", "version", "runtime", "reason"),
    [
        ("Darwin", "arm64", "26.0", True, None),
        ("Darwin", "arm64", "26.2", False, "missing_package"),
        ("Darwin", "arm64", "15.7", True, "unsupported_os_version"),
        ("Darwin", "x86_64", "26.0", True, "unsupported_architecture"),
        ("Linux", "x86_64", "", False, "unsupported_platform"),
    ],
)
def test_apple_speech_is_available_only_on_supported_macs(
    system, machine, version, runtime, reason
):
    environment = capabilities.CapabilityEnvironment(
        system=system,
        machine=machine,
        package_available=lambda _: True,
        providers=frozenset(),
        macos_version=version,
        apple_speech_runtime=runtime,
    )
    capability = capabilities.capability_for("apple_speech", environment=environment)
    assert capability.availability == ("available" if reason is None else "unavailable")
    assert capability.reason == reason
    assert capability.download.state == "not_required"


def test_transcript_cache_key_uses_track_language_and_override(monkeypatch):
    monkeypatch.setattr(audio.settings, "apple_speech_locale", None)
    monkeypatch.setattr(audio.settings, "whisper_model", "base")
    assert audio.transcription_cache_model_key("apple_speech", audio_language="und") == (
        "apple_speech_v1.en-us"
    )
    assert audio.transcription_cache_model_key("apple_speech", audio_language="pt_BR") == (
        "apple_speech_v1.pt-br"
    )
    monkeypatch.setattr(audio.settings, "apple_speech_locale", "es-MX")
    assert audio.transcription_cache_model_key("apple_speech", audio_language="eng") == (
        "apple_speech_v1.es-mx"
    )
    assert audio.transcription_cache_model_key("whisper", audio_language="eng") == "whisper_base"


def test_native_transcript_drives_word_findings_and_is_reused(monkeypatch, tmp_path):
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(64_000))
    cache = tmp_path / "transcript.json"
    checked = []

    def run_native(command, *, should_cancel):
        _, locale, source, output = command
        assert locale == "eng"
        assert Path(source).suffix == ".wav"
        assert should_cancel is cancel_check
        checked.append(source)
        segment = {
            "text": "Darn again.",
            "start": 0.4, "end": 1.5,
            "words": [
                {"word": "Darn", "start": 0.4, "end": 0.8},
                {"word": " again.", "start": 0.8, "end": 1.5},
            ],
        }
        Path(output).write_text(json.dumps([segment]), encoding="utf-8")

    def cancel_check():
        return False

    monkeypatch.setattr(apple_speech.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(apple_speech.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(apple_speech.platform, "mac_ver", lambda: ("26.0", (), "arm64"))
    monkeypatch.setattr(apple_speech, "resolve_helper", lambda *a, **kw: tmp_path / "helper")
    monkeypatch.setattr(apple_speech, "run_command", run_native)
    monkeypatch.setattr(audio.settings, "apple_speech_locale", None)
    monkeypatch.setattr(audio, "_audio_filter_detector", lambda _: lambda _word: False)
    for _ in range(2):
        findings = audio.scan_audio(
            str(path),
            "test",
            model_name="apple_speech",
            deny_words=["darn"],
            transcript_cache_path=str(cache),
            audio_padding_seconds=0,
            audio_language="eng",
            should_cancel=cancel_check,
        )
        assert [(f.label, f.start_seconds, f.end_seconds) for f in findings] == [
            ("darn", 0.4, 0.8),
        ]
    assert len(checked) == 1
    assert json.loads(cache.read_text())[0]["words"][0]["word"] == "Darn"


def test_chunk_overlap_discards_apple_speech_words_and_text_outside_primary_range():
    segments = [{
        "text": "She said darn.",
        "start_seconds": 0.2, "end_seconds": 1.6,
        "words": [
            {"word": "She", "start_seconds": 0.2, "end_seconds": 0.5},
            {"word": " said", "start_seconds": 0.5, "end_seconds": 0.8},
            {"word": " darn.", "start_seconds": 0.8, "end_seconds": 1.6},
        ],
    }]
    assert audio._offset_and_trim_segments(segments, 9, 10, 12, True, trim_timed_text=True) == [
        {
            "text": "darn.",
            "start_seconds": 9.8,
            "end_seconds": 10.6,
            "words": [{"word": " darn.", "start_seconds": 9.8, "end_seconds": 10.6}],
        },
    ]
