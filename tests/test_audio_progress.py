import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.models import FindingCreate, McfCategoryFamily
from scanner.pipeline import audio


def test_legacy_whisper_alias_uses_the_base_model(monkeypatch):
    audio._models.clear()
    requested_models: list[str] = []

    class FakeWhisperModel:
        def __init__(self, model_name, **_kwargs):
            requested_models.append(model_name)

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    monkeypatch.setattr(
        audio,
        "acquire_backend",
        lambda _backend_id: {"faster-whisper-base": Path("/managed/faster-whisper-base")},
    )

    audio._get_whisper_model("whisper")

    assert len(requested_models) == 1
    assert requested_models[0].replace("\\", "/").endswith(
        "/managed/faster-whisper-base"
    )


@pytest.mark.parametrize(
    ("target_id", "advertised_device"),
    [("windows-x64", "cpu"), ("linux-x86_64", "cpu"), ("windows-x64", "cuda")],
)
def test_native_windows_and_linux_whisper_use_the_truthful_device(
    monkeypatch, target_id, advertised_device
):
    audio._models.clear()
    received_kwargs = {}

    class FakeWhisperModel:
        def __init__(self, _model_name, **kwargs):
            received_kwargs.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=FakeWhisperModel),
    )
    monkeypatch.setattr(
        audio,
        "current_environment",
        lambda: SimpleNamespace(target_id=target_id),
    )
    monkeypatch.setattr(
        audio,
        "capability_for",
        lambda _backend_id, *, environment: SimpleNamespace(device=advertised_device),
    )
    monkeypatch.setattr(
        audio,
        "acquire_backend",
        lambda _backend_id: {"faster-whisper-base": Path("/managed/faster-whisper-base")},
    )

    audio._get_whisper_model("whisper")

    assert received_kwargs["device"] == advertised_device


def test_audio_model_normalization_supports_mlx_aliases():
    assert audio.resolve_audio_model_name("whisper") == "whisper"
    assert audio.resolve_audio_model_name("mlx_whisper") == "mlx_whisper"
    assert audio.resolve_audio_model_name("mlx-whisper") == "mlx_whisper"


def test_audio_model_normalization_rejects_unknown_model():
    with pytest.raises(ValueError, match="Unsupported audio model"):
        audio.resolve_audio_model_name("unknown")


def test_mlx_whisper_model_name_uses_the_converted_checkpoint():
    assert audio.resolve_mlx_whisper_model_name("whisper") == "mlx-community/whisper-base-mlx"
    assert audio.resolve_mlx_whisper_model_name("small") == "mlx-community/whisper-small-mlx"
    assert (
        audio.resolve_mlx_whisper_model_name("mlx-community/custom-whisper")
        == "mlx-community/custom-whisper"
    )


def test_audio_scan_supports_mlx_whisper_backend(monkeypatch):
    audio._audio_filter_detectors.clear()
    audio._models.clear()

    received_kwargs = {}

    class FakeMlxWhisperModule:
        @staticmethod
        def transcribe(_path, **kwargs):
            received_kwargs.update(kwargs)
            return {
                "segments": [
                    {
                        "text": "dang",
                        "start": 1.0,
                        "end": 1.4,
                        "words": [
                            {"word": "dang", "start": 1.0, "end": 1.4},
                        ],
                    }
                ]
            }

    monkeypatch.setitem(sys.modules, "mlx_whisper", FakeMlxWhisperModule)
    monkeypatch.setattr(audio.profanity, "contains_profanity", lambda _word: True)
    monkeypatch.setattr(
        audio,
        "acquire_backend",
        lambda _backend_id: {"mlx-whisper-base": Path("/managed/mlx-whisper-base")},
    )

    findings = audio.scan_audio("example.wav", "scan-1", "mlx_whisper")

    assert len(findings) == 1
    assert findings[0].label == "dang"
    assert received_kwargs["word_timestamps"] is True
    assert received_kwargs["path_or_hf_repo"].replace("\\", "/").endswith(
        "/managed/mlx-whisper-base"
    )


@pytest.mark.parametrize(("padding", "bounds"), [
    (None, [12.24, 12.88]), (0.0, [12.34, 12.78]), (0.35, [11.99, 13.13]),
])
def test_audio_findings_pad_raw_whisper_word_boundaries(monkeypatch, padding, bounds):
    audio._audio_filter_detectors.clear()
    word = SimpleNamespace(word="fuck", start=12.34, end=12.78)
    segment = SimpleNamespace(words=[word], text="fuck")
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)
    monkeypatch.setattr(audio.profanity, "contains_profanity", lambda _word: True)

    findings = audio.scan_audio("example.wav", "scan-1", "whisper", audio_padding_seconds=padding)

    assert audio.settings.profanity_pad_seconds == 0.1
    boundaries = [
        value
        for finding in findings
        for value in (finding.start_seconds, finding.end_seconds)
    ]
    assert boundaries == pytest.approx(bounds)


def test_audio_scan_supports_glin_filter(monkeypatch):
    audio._audio_filter_detectors.clear()
    word = SimpleNamespace(word="dang", start=4.0, end=4.5)
    segment = SimpleNamespace(words=[word], text="dang")
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)
    monkeypatch.setitem(
        sys.modules,
        "glin_profanity",
        SimpleNamespace(contains_profanity=lambda text: text == "dang"),
    )

    findings = audio.scan_audio("example.wav", "scan-1", "whisper", "glin_profanity")

    assert len(findings) == 1
    assert findings[0].label == "dang"


def test_audio_scan_supports_glin_filter_class_api(monkeypatch):
    audio._audio_filter_detectors.clear()
    word = SimpleNamespace(word="darn", start=1.0, end=1.3)
    segment = SimpleNamespace(words=[word], text="darn")
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)

    class FakeFilter:
        def check_profanity(self, text: str):
            return {
                "contains_profanity": text == "darn",
                "profane_words": [text] if text == "darn" else [],
            }

    monkeypatch.setitem(
        sys.modules,
        "glin_profanity",
        SimpleNamespace(Filter=FakeFilter),
    )

    findings = audio.scan_audio("example.wav", "scan-1", "whisper", "glin_profanity")

    assert len(findings) == 1
    assert findings[0].label == "darn"


def test_audio_scan_supports_alt_profanity_check_filter(monkeypatch):
    audio._audio_filter_detectors.clear()
    word = SimpleNamespace(word="crud", start=6.0, end=6.5)
    segment = SimpleNamespace(words=[word], text="crud")
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)
    monkeypatch.setitem(
        sys.modules,
        "profanity_check",
        SimpleNamespace(contains_profanity=lambda text: text == "crud"),
    )

    findings = audio.scan_audio("example.wav", "scan-1", "whisper", "alt-profanity-check")

    assert len(findings) == 1
    assert findings[0].label == "crud"


def test_audio_scan_supports_alt_profanity_check_predict_api(monkeypatch):
    audio._audio_filter_detectors.clear()
    word = SimpleNamespace(word="crud", start=6.0, end=6.5)
    segment = SimpleNamespace(words=[word], text="crud")
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)

    def fake_predict(texts):
        assert texts == ["crud"]
        return [1]

    monkeypatch.setitem(
        sys.modules,
        "profanity_check",
        SimpleNamespace(predict=fake_predict),
    )

    findings = audio.scan_audio("example.wav", "scan-1", "whisper", "alt-profanity-check")

    assert len(findings) == 1
    assert findings[0].label == "crud"


def test_audio_scan_supports_alt_profanity_check_array_like_predict_result(monkeypatch):
    audio._audio_filter_detectors.clear()
    word = SimpleNamespace(word="crud", start=6.0, end=6.5)
    segment = SimpleNamespace(words=[word], text="crud")
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)

    class FakeArray:
        def tolist(self):
            return [1]

    def fake_predict(texts):
        assert texts == ["crud"]
        return FakeArray()

    monkeypatch.setitem(
        sys.modules,
        "profanity_check",
        SimpleNamespace(predict=fake_predict),
    )

    findings = audio.scan_audio("example.wav", "scan-1", "whisper", "alt-profanity-check")

    assert len(findings) == 1
    assert findings[0].label == "crud"


def test_audio_scan_strips_punctuation_from_flagged_word_labels(monkeypatch):
    audio._audio_filter_detectors.clear()
    word = SimpleNamespace(word="crud!!!", start=6.0, end=6.5)
    segment = SimpleNamespace(words=[word], text="crud!!!")
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)
    monkeypatch.setitem(
        sys.modules,
        "profanity_check",
        SimpleNamespace(contains_profanity=lambda text: text in {"crud", "crud!!!"}),
    )

    findings = audio.scan_audio("example.wav", "scan-1", "whisper", "alt-profanity-check")

    assert len(findings) == 1
    assert findings[0].label == "crud"


def test_audio_scan_rejects_unknown_filter(monkeypatch):
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)

    try:
        audio.scan_audio("example.wav", "scan-1", "whisper", "unknown-filter")
    except ValueError as exc:
        assert "Unsupported audio filter" in str(exc)
    else:
        raise AssertionError("Expected scan_audio to reject unknown audio filters")


def test_audio_scan_flags_runtime_deny_words_even_when_filter_does_not_match(monkeypatch):
    audio._audio_filter_detectors.clear()
    word = SimpleNamespace(word="newcurse", start=2.0, end=2.4)
    segment = SimpleNamespace(words=[word], text="newcurse")
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)
    monkeypatch.setattr(audio.profanity, "contains_profanity", lambda _text: False)

    findings = audio.scan_audio(
        "example.wav",
        "scan-1",
        model_name="whisper",
        filter_name="better_profanity",
        deny_words=["newcurse"],
    )

    assert len(findings) == 1
    assert findings[0].label == "newcurse"


def test_audio_scan_flags_runtime_deny_words_from_segment_without_word_timestamps(monkeypatch):
    audio._audio_filter_detectors.clear()
    segment = SimpleNamespace(words=[], text="keep this newcurse hidden", start=1.0, end=2.0)
    model = SimpleNamespace(transcribe=lambda *_args, **_kwargs: ([segment], None))
    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: model)
    monkeypatch.setattr(audio.profanity, "contains_profanity", lambda _text: False)

    findings = audio.scan_audio(
        "example.wav",
        "scan-1",
        model_name="whisper",
        filter_name="better_profanity",
        deny_words=["newcurse"],
    )

    assert len(findings) == 1
    assert findings[0].label == "newcurse"


def test_audio_allow_defaults_auto_deselect_matching_findings_without_deny_entries():
    findings = [
        FindingCreate(
            video_id="scan-1",
            category=McfCategoryFamily.LANGUAGE,
            subcategory="swearing",
            start_seconds=1.0,
            end_seconds=1.5,
            confidence=0.9,
            label="dang",
            source="audio",
            selected=True,
        ),
        FindingCreate(
            video_id="scan-1",
            category=McfCategoryFamily.LANGUAGE,
            subcategory="swearing",
            start_seconds=2.0,
            end_seconds=2.5,
            confidence=0.9,
            label="other",
            source="audio",
            selected=True,
        ),
    ]

    adjusted = audio.apply_audio_allow_defaults(
        findings,
        allow_words=["dang", "hell"],
        deny_words=["hell"],
    )
    # Only overlapping words are excluded from runtime Allow.
    assert [finding.selected for finding in adjusted] == [False, True]

    adjusted_without_deny = audio.apply_audio_allow_defaults(
        findings,
        allow_words=["dang"],
        deny_words=[],
    )
    assert [finding.selected for finding in adjusted_without_deny] == [False, True]


def test_audio_allow_defaults_keeps_word_selected_when_allow_word_is_also_in_deny():
    findings = [
        FindingCreate(
            video_id="scan-1",
            category=McfCategoryFamily.LANGUAGE,
            subcategory="swearing",
            start_seconds=1.0,
            end_seconds=1.5,
            confidence=0.9,
            label="dang",
            source="audio",
            selected=True,
        )
    ]

    adjusted = audio.apply_audio_allow_defaults(
        findings,
        allow_words=["dang"],
        deny_words=["dang"],
    )

    assert adjusted[0].selected is True


def test_effective_audio_filter_words_dedupes_allow_words_present_in_deny():
    runtime_allow, runtime_deny = audio.effective_audio_filter_words(
        allow_words=["dang", "heck"],
        deny_words=["heck", "crud"],
    )

    assert runtime_allow == {"dang"}
    assert runtime_deny == {"heck", "crud"}


def test_effective_audio_filter_words_normalizes_punctuated_entries():
    runtime_allow, runtime_deny = audio.effective_audio_filter_words(
        allow_words=["f-word", "dang!!!"],
        deny_words=["new.curse"],
    )

    assert runtime_allow == {"fword", "dang"}
    assert runtime_deny == {"newcurse"}


def test_audio_allow_defaults_matches_punctuated_allow_entry_to_normalized_label():
    findings = [
        FindingCreate(
            video_id="scan-1",
            category=McfCategoryFamily.LANGUAGE,
            subcategory="swearing",
            start_seconds=1.0,
            end_seconds=1.5,
            confidence=0.9,
            label="fword",
            source="audio",
            selected=True,
        )
    ]

    adjusted = audio.apply_audio_allow_defaults(
        findings,
        allow_words=["f-word"],
        deny_words=[],
    )

    assert adjusted[0].selected is False


def test_audio_extraction_reports_ffmpeg_time_progress(monkeypatch):
    progress: list[tuple[float, str]] = []
    command: list[str] = []

    def fake_run_command(args, **kwargs):
        command.extend(args)
        Path(args[-2]).write_bytes(b"wav")
        callback = kwargs["output_callback"]
        callback("out_time_us=30000000")
        callback("out_time_us=120000000")

    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg"),
    )
    monkeypatch.setattr(audio, "run_command", fake_run_command)

    assert audio.extract_audio(
        "example.mkv",
        "audio.wav",
        duration_seconds=120,
        progress_callback=lambda fraction, message: progress.append((fraction, message)),
    ) == "audio.wav"

    assert command[command.index("-progress") + 1] == "pipe:2"
    assert command[command.index("-f") + 1] == "wav"
    assert command[-2].endswith(".audio.partial.wav")
    assert progress == [
        (0.25, "Extracting audio (30s/120s)"),
        (1.0, "Extracting audio (120s/120s)"),
    ]


def test_audio_transcription_cache_is_reused_for_same_model(monkeypatch, tmp_path):
    transcript_calls = {"count": 0}
    word = SimpleNamespace(word="dang", start=4.0, end=4.5)
    segment = SimpleNamespace(words=[word], text="dang", start=4.0, end=4.5)

    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            transcript_calls["count"] += 1
            return [segment], None

    monkeypatch.setattr(audio, "_get_whisper_model", lambda _model_name: FakeModel())
    monkeypatch.setattr(
        audio,
        "_audio_filter_detector",
        lambda filter_name: (lambda _text: filter_name == "match"),
    )

    transcript_cache = tmp_path / "transcript_base.json"

    findings_first = audio.scan_audio(
        "example.wav",
        "scan-1",
        model_name="whisper",
        filter_name="match",
        transcript_cache_path=str(transcript_cache),
    )
    findings_second = audio.scan_audio(
        "example.wav",
        "scan-1",
        model_name="whisper",
        filter_name="no-match",
        transcript_cache_path=str(transcript_cache),
    )

    assert transcript_calls["count"] == 1
    assert len(findings_first) == 1
    assert findings_second == []


def test_chunked_transcription_reports_completed_chunk_progress(monkeypatch, tmp_path):
    audio_path = tmp_path / "audio.wav"
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(1)
        output.writeframes(b"\0\0" * 5)

    monkeypatch.setattr(audio, "_TRANSCRIPT_CHUNK_SECONDS", 2)
    monkeypatch.setattr(audio, "_TRANSCRIPT_CHUNK_OVERLAP_SECONDS", 0)
    monkeypatch.setattr(audio, "_transcribe_serialized", lambda *_args: [])
    progress: list[tuple[float, str]] = []

    audio._transcribe_with_resumable_chunks(
        str(audio_path),
        "whisper",
        str(tmp_path / "transcript.json"),
        progress_callback=lambda fraction, message: progress.append((fraction, message)),
    )

    assert progress == [
        (0.4, "Transcribed audio chunk 1/3"),
        (0.8, "Transcribed audio chunk 2/3"),
        (1.0, "Transcribed audio chunk 3/3"),
    ]
