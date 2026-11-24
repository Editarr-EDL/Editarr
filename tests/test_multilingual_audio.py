"""Multilingual discovery, grounded timestamps, and graceful Qwen fallbacks."""

import json
from types import SimpleNamespace

import pytest

from scanner.model_acquisition import AcquisitionCancelled
from scanner.models import FindingCreate, McfCategoryFamily
from scanner.pipeline import audio, qwen_language
from scanner.pipeline.qwen_language_detection import (
    TranscriptWord,
    _parse_detection_response,
    transcript_windows,
)


def _segment(words, *, start=1):
    return {
        "text": "".join(words),
        "start_seconds": start,
        "end_seconds": start + len(words),
        "words": [
            {"word": word, "start_seconds": start + index, "end_seconds": start + index + 0.5}
            for index, word in enumerate(words)
        ],
    }


def _response(matches, identifier=0):
    return json.dumps({"version": 1, "results": [{"id": identifier, "matches": matches}]})


def _match(first=0, last=0, category="swearing", severity="high"):
    return {"first": first, "last": last, "subcategory": category, "severity": severity}


def _install_qwen(monkeypatch, generate):
    monkeypatch.setattr(
        qwen_language,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available"),
    )
    monkeypatch.setattr(qwen_language.qwen_summary, "_load_model", lambda _: (None, None, None))
    monkeypatch.setattr(
        qwen_language.qwen_summary,
        "_require_dependency",
        lambda name, _: (
            SimpleNamespace(
                generate=lambda _m, _p, prompt, **_kw: SimpleNamespace(text=generate(prompt))
            )
            if name == "mlx_vlm"
            else SimpleNamespace(apply_chat_template=lambda _p, _c, prompt, **_kw: prompt)
        ),
    )


@pytest.mark.parametrize(
    "words,category",
    [
        (["¡Mierda!"], "swearing"),
        (["Dios", " mío!"], "blasphemy"),
        (["Putain!"], "swearing"),
        (["Scheiße!"], "swearing"),
        (["他妈的"], "swearing"),
        (["कमीना"], "nameCalling"),
        (["يا", " إلهي!"], "blasphemy"),
        (["Jesus", " Christ!"], "blasphemy"),
        (["Oh", " merde!"], "swearing"),
    ],
)
def test_audio_discovers_native_language_without_dictionary_hits(monkeypatch, words, category):
    segment = _segment(words)
    prompts, events, progress = [], [], []

    def generate(prompt):
        prompts.append(prompt)
        inputs = json.loads(prompt.split("Inputs: ")[1])
        assert [word["text"] for word in inputs[0]["words"]] == words
        return _response([_match(last=len(words) - 1, category=category)])

    _install_qwen(monkeypatch, generate)
    def reject_dictionary(_):
        pytest.fail("The Qwen filter must not load a dictionary detector")

    monkeypatch.setattr(audio, "_audio_filter_detector", reject_dictionary)
    monkeypatch.setattr(audio, "_transcription_with_cache", lambda *a, **k: [segment])
    findings = audio.scan_audio(
        "audio.wav",
        "scan-1",
        filter_name="qwen",
        qwen_model="qwen",
        provenance_callback=events.append,
        progress_callback=lambda fraction, _: progress.append(fraction),
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.label == "".join(words)
    assert finding.category == McfCategoryFamily.LANGUAGE
    assert finding.subcategory.value == category
    assert finding.severity.value == "high"
    assert finding.start_seconds == pytest.approx(0.9)
    assert finding.end_seconds == pytest.approx(len(words) + 0.6)
    assert finding.selected is True
    assert events == ["started", "complete"]
    assert progress == sorted(progress) and progress[-1] == 1.0
    assert "ordinary religious discussion" in prompts[0]
    assert "quoted data" in prompts[0]
    assert "any language" in prompts[0]
    assert audio.apply_audio_allow_defaults(findings, [finding.label], [])[0].selected is False
    assert audio.apply_audio_allow_defaults(findings, [finding.label], [finding.label])[0].selected


@pytest.mark.parametrize(
    "filter_name", [None, "better_profanity", "glin_profanity", "alt_profanity_check"]
)
def test_dictionary_filters_do_not_run_whole_transcript_discovery(monkeypatch, filter_name):
    monkeypatch.setattr(audio.settings, "audio_filter", "better_profanity")
    monkeypatch.setattr(audio, "_audio_filter_detector", lambda _: lambda _: False)
    monkeypatch.setattr(audio, "_transcription_with_cache", lambda *a, **k: [_segment(["Bonjour"])])

    def categorize(findings, _model, **kwargs):
        assert kwargs["transcript_segments"] is None
        return findings

    monkeypatch.setattr(audio, "categorize_language_findings", categorize)
    assert audio.scan_audio("audio.wav", "scan-1", filter_name=filter_name, qwen_model="qwen") == []


def test_clean_transcript_is_successful_empty_coverage(monkeypatch):
    _install_qwen(monkeypatch, lambda _: _response([]))
    events = []
    assert (
        qwen_language.categorize_language_findings(
            [],
            "qwen",
            transcript_segments=[_segment(["Bonjour"])],
            video_id="scan-1",
            provenance_callback=events.append,
        )
        == []
    )
    assert events == ["started", "complete"]


@pytest.mark.parametrize("padding", [None, 0, 0.45])
def test_discovery_falls_back_to_segment_timing(monkeypatch, padding):
    _install_qwen(monkeypatch, lambda _: _response([_match(1, 2, "blasphemy")]))
    findings = qwen_language.categorize_language_findings(
        [],
        "qwen",
        transcript_segments=[
            {
                "text": "¡Oh Dios mío!",
                "start_seconds": 12,
                "end_seconds": 14,
            }
        ],
        video_id="scan-1",
        audio_padding_seconds=padding,
    )
    effective_padding = 0.1 if padding is None else padding
    assert findings[0].label == "Dios mío!"
    assert findings[0].start_seconds == pytest.approx(12 - effective_padding)
    assert findings[0].end_seconds == pytest.approx(14 + effective_padding)


@pytest.mark.parametrize(
    "response",
    [
        None,
        "invalid",
        _response([_match(-1)]),
        _response([_match(last=5)]),
        _response([_match(first=True)]),
        _response([_match(category="violence")]),
        _response([_match(severity="extreme")]),
        _response([_match(), _match()]),
        _response([], identifier=True),
        _response([], identifier=9),
        '{"version":1,"results":[]}',
        '{"version":true,"results":[{"id":0,"matches":[]}]}',
        '{"version":1,"results":[{"id":0,"matches":[{"first":0}]}]}',
    ],
)
def test_discovery_rejects_invalid_or_ungrounded_spans(response):
    assert _parse_detection_response(response, [[TranscriptWord("merde", 1, 2)]]) is None


@pytest.mark.parametrize("error,expected", [("invalid", "failed"), (RuntimeError(), "failed")])
def test_discovery_failure_does_not_claim_clean_success(monkeypatch, error, expected):
    def generate(_):
        if isinstance(error, Exception):
            raise error
        return error

    _install_qwen(monkeypatch, generate)
    events = []
    assert (
        qwen_language.categorize_language_findings(
            [],
            "qwen",
            transcript_segments=[_segment(["merde"])],
            video_id="scan-1",
            provenance_callback=events.append,
        )
        == []
    )
    assert events == ["started", expected]


def test_discovery_reports_partial_after_valid_clean_batch(monkeypatch):
    def generate(prompt):
        inputs = json.loads(prompt.split("\nInputs: ", 1)[1])
        if len(inputs) == 1:
            return "invalid"
        return json.dumps(
            {"version": 1, "results": [{"id": window["id"], "matches": []} for window in inputs]}
        )

    _install_qwen(monkeypatch, generate)
    events = []
    qwen_language.categorize_language_findings(
        [],
        "qwen",
        transcript_segments=[_segment(["bonjour"])] * 5,
        video_id="scan-1",
        provenance_callback=events.append,
    )
    assert events[-1] == "partial"


@pytest.mark.parametrize("recover_singleton", [True, False])
def test_discovery_retries_rejected_batches_and_preserves_window_timing(
    monkeypatch, recover_singleton
):
    segments = [
        _segment([word], start=start)
        for start, word in enumerate(["Merde!", "Bonjour", "Scheiße!", "Putain!", "Merci"], 10)
    ]
    failed_window_prompts, attempts, events, progress = [], [], [], []

    def generate(prompt):
        inputs = json.loads(prompt.split("\nInputs: ", 1)[1])
        attempts.append(len(inputs))
        if len(inputs) > 1:
            return "invalid"
        word = inputs[0]["words"][0]["text"]
        if word == "Scheiße!":
            failed_window_prompts.append(prompt)
            if not recover_singleton or len(failed_window_prompts) == 1:
                return "invalid"
        return _response([] if word in {"Bonjour", "Merci"} else [_match()])

    _install_qwen(monkeypatch, generate)
    findings = qwen_language.categorize_language_findings(
        [],
        "qwen",
        transcript_segments=segments,
        video_id="scan-1",
        audio_padding_seconds=0,
        provenance_callback=events.append,
        progress_callback=lambda fraction, _: progress.append(fraction),
    )

    expected = [("Merde!", 10, 10.5), ("Putain!", 13, 13.5)]
    if recover_singleton:
        expected.insert(1, ("Scheiße!", 12, 12.5))
    assert [(item.label, item.start_seconds, item.end_seconds) for item in findings] == expected
    assert events == ["started", "complete" if recover_singleton else "partial"]
    assert attempts == [4, 2, 1, 1, 2, 1, 1, 1, 1]
    assert progress == pytest.approx([0.8, 1.0])

    initial, retry = failed_window_prompts
    assert initial != retry
    assert initial.split("\nInputs: ", 1)[1] == retry.split("\nInputs: ", 1)[1]


def test_invalid_categorization_keeps_detector_and_reports_failure(monkeypatch):
    _install_qwen(monkeypatch, lambda _: "invalid")
    finding = FindingCreate(
        video_id="scan-1",
        source="audio",
        category="language",
        subcategory="swearing",
        severity="medium",
        start_seconds=0.9,
        end_seconds=1.6,
        confidence=0.9,
        label="merde",
    )
    events = []
    assert qwen_language.categorize_language_findings(
        [finding],
        "qwen",
        provenance_callback=events.append,
    ) == [finding]
    assert events[-1] == "failed"


def test_existing_detector_occurrence_is_not_duplicated(monkeypatch):
    def generate(prompt):
        if prompt.startswith("Find profanity"):
            return _response([_match(), _match(1, 1)])
        return json.dumps(
            {
                "version": 2,
                "results": [
                    {"id": 0, "subcategory": "swearing", "severity": "high"},
                ],
            }
        )

    _install_qwen(monkeypatch, generate)
    finding = FindingCreate(
        video_id="scan-1",
        source="audio",
        category="language",
        subcategory="swearing",
        severity="medium",
        start_seconds=0.9,
        end_seconds=1.6,
        confidence=0.9,
        label="merde",
        selected=False,
    )
    findings = qwen_language.categorize_language_findings(
        [finding],
        "qwen",
        transcript_segments=[_segment(["Merde!", " merde!"])],
        video_id="scan-1",
    )
    assert len(findings) == 2
    assert findings[0].selected is False
    assert findings[0].severity.value == "high"
    assert findings[1].start_seconds == pytest.approx(1.9)


def test_discovery_propagates_cancellation(monkeypatch):
    def generate(_):
        raise AcquisitionCancelled("qwen")

    _install_qwen(monkeypatch, generate)
    with pytest.raises(AcquisitionCancelled):
        qwen_language.categorize_language_findings(
            [],
            "qwen",
            transcript_segments=[_segment(["merde"])],
            video_id="scan-1",
        )


def test_unavailable_qwen_reports_unsupported_even_without_dictionary_findings(monkeypatch):
    monkeypatch.setattr(
        qwen_language,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="unavailable"),
    )
    events = []
    assert (
        qwen_language.categorize_language_findings(
            [],
            "qwen",
            transcript_segments=[_segment(["merde"])],
            video_id="scan-1",
            provenance_callback=events.append,
        )
        == []
    )
    assert events == ["unsupported"]


def test_windows_cover_long_transcripts_with_overlap_and_bounded_input():
    words = [f" word{i}" for i in range(150)]
    windows = transcript_windows([_segment(words)])
    assert all(len(window) <= 64 for window in windows)
    assert {word.text for window in windows for word in window} == set(words)
    assert windows[0][-8:] == windows[1][:8]
    long_text = "你好" * 5000
    windows = transcript_windows([{"text": long_text, "start_seconds": 2, "end_seconds": 30}])
    assert all(sum(len(word.text) for word in window) <= 2000 for window in windows)
    assert windows[-1][-1].end == 30


@pytest.mark.parametrize(
    "word,variant",
    [
        ("कमीना", "कमीना!"),
        ("putain", "PUTAIN!"),
        ("scheiße", "SCHEISSE!"),
        ("cabrón", "cabro\u0301n!"),
        ("他妈的", "他妈的！"),
    ],
)
def test_unicode_allow_deny_matches_with_and_without_word_timestamps(monkeypatch, word, variant):
    monkeypatch.setattr(audio, "_audio_filter_detector", lambda _: lambda _: False)
    for segment in [_segment([variant]), {"text": variant, "start_seconds": 1, "end_seconds": 2}]:
        monkeypatch.setattr(audio, "_transcription_with_cache", lambda *a, **k: [segment])
        findings = audio.scan_audio("audio.wav", "scan-1", deny_words=[word])
        assert len(findings) == 1
        assert audio.apply_audio_allow_defaults(findings, [word], [])[0].selected is False
        assert audio.apply_audio_allow_defaults(findings, [word], [variant])[0].selected is True


@pytest.mark.parametrize(
    "text,words,expected",
    [
        ("他妈的！", ["他", "妈", "的！"], "他妈的！"),
        ("Dios mío!", ["Dios", "mío!"], "Dios mío!"),
    ],
)
def test_discovery_preserves_original_script_spacing(monkeypatch, text, words, expected):
    _install_qwen(monkeypatch, lambda _: _response([_match(last=len(words) - 1)]))
    segment = _segment(words)
    segment["text"] = text
    findings = qwen_language.categorize_language_findings(
        [],
        "qwen",
        transcript_segments=[segment],
        video_id="scan-1",
    )
    assert findings[0].label == expected
