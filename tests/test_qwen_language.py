"""Registry-safe Qwen language classification from transcript context."""

import json
from types import SimpleNamespace

import pytest

from scanner.model_acquisition import AcquisitionCancelled
from scanner.models import FindingCreate, McfCategoryFamily, Severity
from scanner.pipeline import audio, qwen_language


def _finding(word: str, context: str, start: float = 1.0) -> FindingCreate:
    return FindingCreate(
        video_id="scan-1",
        category=McfCategoryFamily.LANGUAGE,
        subcategory="swearing",
        start_seconds=start,
        end_seconds=start + 0.5,
        severity=Severity.MEDIUM,
        confidence=0.9,
        label=word,
        details=context,
        source="audio",
    )


def test_qwen_language_reports_no_input_and_unavailable(monkeypatch):
    events = []
    finding = _finding("jerk", "You are a jerk")
    assert qwen_language.categorize_language_findings(
        [], "qwen3-vl-4b-instruct-4bit", provenance_callback=events.append,
    ) == []
    assert events == ["no_inputs"]
    monkeypatch.setattr(
        qwen_language, "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="unavailable"),
    )
    events.clear()
    assert qwen_language.categorize_language_findings(
        [finding], "qwen3-vl-4b-instruct-4bit", provenance_callback=events.append,
    ) == [finding]
    assert events == ["unsupported"]


@pytest.mark.parametrize(
    "response",
    [
        None,
        "not json",
        '{"version":1,"results":[{"id":0,"subcategory":"swearing","severity":"medium"}]}',
        '{"version":2,"results":[{"id":0,"subcategory":"violence","severity":"medium"}]}',
        '{"version":2,"results":[{"id":0,"subcategory":"swearing","severity":"medium"},{"id":0,"subcategory":"vulgarity","severity":"high"}]}',
        '{"version":2,"results":[{"id":0,"subcategory":"madeUp","severity":"medium"}]}',
        '{"version":2,"results":[{"id":0,"subcategory":"swearing"}]}',
        '{"version":2,"results":[{"id":0,"subcategory":"swearing","severity":"extreme"}]}',
        '{"version":2,"results":[{"id":0,"subcategory":"swearing","severity":"HIGH"}]}',
        '{"version":true,"results":[{"id":0,"subcategory":"swearing","severity":"medium"}]}',
    ],
)
def test_qwen_language_rejects_untrusted_or_incomplete_output(response):
    assert qwen_language._parse_language_response(response, {0}) is None


def test_qwen_language_accepts_only_registry_language_tokens():
    response = json.dumps({
        "version": 2,
        "results": [
            {"id": 0, "subcategory": "nameCalling", "severity": "low"},
            {"id": 1, "subcategory": "sexualDialogue", "severity": "high"},
        ],
    })

    parsed = qwen_language._parse_language_response(response, {0, 1})

    assert parsed is not None
    assert [(category.value, severity.value) for category, severity in parsed.values()] == [
        ("nameCalling", "low"),
        ("sexualDialogue", "high"),
    ]


def test_qwen_language_batches_unique_contexts_and_preserves_finding_fields(monkeypatch):
    prompts: list[str] = []
    selected_models: list[str] = []
    progress: list[float] = []

    def generate(_model, _processor, prompt, **kwargs):
        assert kwargs == {"max_tokens": 512, "temperature": 0.0, "verbose": False}
        prompts.append(prompt)
        inputs = json.loads(prompt.split("Inputs: ", 1)[1])
        return SimpleNamespace(text=json.dumps({
            "version": 2,
            "results": [
                {
                    "id": item["id"],
                    "subcategory": "nameCalling" if item["word"] == "jerk" else "vulgarity",
                    "severity": (
                        "low" if item["word"] == "word-0"
                        else "high" if item["word"] == "word-1"
                        else "medium"
                    ),
                }
                for item in inputs
            ],
        }))

    monkeypatch.setattr(
        qwen_language, "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available"),
    )
    monkeypatch.setattr(
        qwen_language.qwen_summary,
        "_require_dependency",
        lambda name, _hint: (
            SimpleNamespace(generate=generate)
            if name == "mlx_vlm"
            else SimpleNamespace(apply_chat_template=lambda _p, _c, prompt, **_k: prompt)
        ),
    )
    monkeypatch.setattr(
        qwen_language.qwen_summary,
        "_load_model",
        lambda model: selected_models.append(model) or (object(), object(), object()),
    )
    findings = [_finding("jerk", "You are a jerk", 1), _finding("jerk", "You are a jerk", 3)]
    findings += [_finding(f"word-{index}", f"context-{index}", index + 5) for index in range(6)]

    categorized = qwen_language.categorize_language_findings(
        findings,
        "qwen3-vl-4b-instruct-4bit",
        progress_callback=lambda fraction, _message: progress.append(fraction),
    )

    assert selected_models == ["qwen3-vl-4b-instruct-4bit"]
    assert len(prompts) == 2
    assert "nameCalling" in prompts[0] and "sexualDialogue" in prompts[0]
    assert "low for words that are not generally considered vulgar or swearing" in prompts[0]
    assert "permutation of the word 'fuck'" in prompts[0]
    assert "blasphemous references to named deities" in prompts[0]
    assert progress == [6 / 7, 1.0]
    assert categorized[0].subcategory.value == "nameCalling"
    assert categorized[1].subcategory.value == "nameCalling"
    assert all(value.subcategory.value == "vulgarity" for value in categorized[2:])
    assert [value.severity for value in categorized] == [
        Severity.MEDIUM,
        Severity.MEDIUM,
        Severity.LOW,
        Severity.HIGH,
        Severity.MEDIUM,
        Severity.MEDIUM,
        Severity.MEDIUM,
        Severity.MEDIUM,
    ]
    assert categorized[0].model_dump(exclude={"subcategory", "severity"}) == findings[0].model_dump(
        exclude={"subcategory", "severity"}
    )


def test_qwen_language_keeps_detector_result_when_unavailable_or_invalid(monkeypatch):
    finding = _finding("jerk", "You are a jerk")
    monkeypatch.setattr(
        qwen_language, "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="unavailable"),
    )
    assert qwen_language.categorize_language_findings([finding], "qwen") == [finding]

    def probe_failure():
        raise RuntimeError("capability probe failed")

    monkeypatch.setattr(qwen_language, "qwen_enrichment_capability", probe_failure)
    assert qwen_language.categorize_language_findings([finding], "qwen") == [finding]

    monkeypatch.setattr(
        qwen_language, "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available"),
    )
    monkeypatch.setattr(
        qwen_language.qwen_summary,
        "_require_dependency",
        lambda name, _hint: (
            SimpleNamespace(generate=lambda *_args, **_kwargs: SimpleNamespace(text=json.dumps({
                "version": 2,
                "results": [{"id": 0, "subcategory": "nameCalling", "severity": "extreme"}],
            })))
            if name == "mlx_vlm"
            else SimpleNamespace(apply_chat_template=lambda _p, _c, prompt, **_k: prompt)
        ),
    )
    monkeypatch.setattr(
        qwen_language.qwen_summary,
        "_load_model",
        lambda _model: (object(), object(), object()),
    )
    assert qwen_language.categorize_language_findings([finding], "qwen") == [finding]


def test_qwen_language_preserves_acquisition_cancellation(monkeypatch):
    monkeypatch.setattr(
        qwen_language, "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available"),
    )
    monkeypatch.setattr(
        qwen_language.qwen_summary,
        "_require_dependency",
        lambda _name, _hint: object(),
    )

    def cancelled(_model):
        raise AcquisitionCancelled("qwen3-vl-8b-instruct-4bit")

    monkeypatch.setattr(qwen_language.qwen_summary, "_load_model", cancelled)
    with pytest.raises(AcquisitionCancelled):
        qwen_language.categorize_language_findings(
            [_finding("jerk", "You are a jerk")], "qwen3-vl-8b-instruct-4bit"
        )


def test_audio_scan_passes_flagged_language_to_qwen(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(audio, "_audio_filter_detector", lambda _name: lambda word: word == "jerk")
    monkeypatch.setattr(
        audio,
        "_transcription_with_cache",
        lambda *_args, **_kwargs: [{
            "text": "You are a jerk",
            "words": [{"word": "jerk", "start_seconds": 1.0, "end_seconds": 1.3}],
        }],
    )

    def categorize(
        findings, model, *, progress_callback, transcript_segments, video_id, audio_padding_seconds
    ):
        assert video_id == "scan-1"
        assert transcript_segments is None
        assert audio_padding_seconds == 0.1
        seen["model"] = model
        seen["context"] = findings[0].details
        progress_callback(1.0, "Categorizing language")
        return [findings[0].model_copy(update={
            "subcategory": "nameCalling",
            "severity": Severity.LOW,
        })]

    monkeypatch.setattr(audio, "categorize_language_findings", categorize)
    findings = audio.scan_audio(
        "audio.wav", "scan-1", qwen_model="qwen3-vl-4b-instruct-4bit",
        progress_callback=lambda _fraction, _message: None,
    )

    assert seen == {"model": "qwen3-vl-4b-instruct-4bit", "context": "You are a jerk"}
    assert findings[0].subcategory == "nameCalling"
    assert findings[0].severity == Severity.LOW
