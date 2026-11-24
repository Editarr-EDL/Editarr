"""Verify recorded and legacy replay inspection through the language pipeline."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.evaluation.qwen_language_inspection import (
    InspectionTarget,
    InspectionTestEvidence,
    ask_qwen,
    inspect_context,
    rerun_inspection,
)
from scanner.evaluation.qwen_language_replay import LanguageReplayReport, replay_qwen_language
from scanner.model_acquisition import manifest
from scanner.pipeline import audio, qwen_language
from scanner.settings.capabilities import DEFAULT_QWEN_MODEL

_JSON_INPUT = "\nInputs: "


def _segment(text, start):
    return {
        "text": text,
        "start_seconds": start,
        "end_seconds": start + 1,
        "words": [{"word": text, "start_seconds": start, "end_seconds": start + 1}],
    }


@pytest.fixture
def run_report(tmp_path, monkeypatch):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake test audio")
    cache_dir = tmp_path / "cache"
    segments = [_segment("clean", 10), _segment("fool", 12), _segment("other", 14)]

    def transcript(_audio_path, _model_name, *, transcript_cache_path, **_kwargs):
        Path(transcript_cache_path).write_text(json.dumps(segments), encoding="utf-8")
        return segments

    monkeypatch.setattr(audio, "_transcription_with_cache", transcript)
    monkeypatch.setattr(
        qwen_language,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available"),
    )

    def run(generate, deny_words=None):
        monkeypatch.setattr(qwen_language, "load_language_generator", lambda _: generate)
        return replay_qwen_language(
            audio_path,
            cache_dir=cache_dir,
            audio_model="whisper",
            qwen_model=DEFAULT_QWEN_MODEL,
            audio_padding_seconds=0.15,
            deny_words=deny_words,
        )

    return run, cache_dir


def _detection_reply(prompt, *, flag_fool=True):
    inputs = json.loads(prompt.split(_JSON_INPUT)[1])
    return json.dumps(
        {
            "version": 1,
            "results": [
                {
                    "id": window["id"],
                    "matches": (
                        [{"first": 0, "last": 0, "subcategory": "nameCalling", "severity": "low"}]
                        if flag_fool and window["words"][0]["text"] == "fool"
                        else []
                    ),
                }
                for window in inputs
            ],
        }
    )


def test_recorded_detection_inspection_has_verdicts_inputs_and_retries(run_report):
    run, cache_dir = run_report
    replies = []

    def generate(prompt, max_tokens=2048):
        assert max_tokens == 2048
        response = "invalid first reply" if not replies else _detection_reply(prompt)
        replies.append(response)
        return response

    report = LanguageReplayReport.model_validate_json(run(generate).model_dump_json())
    assert report.status == "complete"
    finding = report.findings[0]
    context = inspect_context(
        report,
        InspectionTarget(
            start_seconds=finding.start_seconds,
            end_seconds=finding.end_seconds,
            label=finding.label,
        ),
        cache_dir=cache_dir,
    )
    assert context.evidence == "recorded"
    assert context.finding == finding
    assert context.flagged and context.accepted
    assert [attempt.accepted for attempt in context.attempts] == [False, True]
    assert context.attempts[0].response == "invalid first reply"
    assert context.response == replies[-1]
    assert [words[0].start for words in context.windows] == [12, 14]
    assert [window["words"][0]["text"] for window in context.inputs] == ["fool", "other"]

    missed = inspect_context(
        report, InspectionTarget(start_seconds=10, label="clean"), cache_dir=cache_dir
    )
    assert not missed.flagged and missed.finding is None
    assert json.loads(missed.response)["results"] == [{"id": 0, "matches": []}]


def test_legacy_inspection_requires_original_cached_transcript(run_report):
    run, cache_dir = run_report
    saved = run(lambda prompt, **_: _detection_reply(prompt))
    legacy = LanguageReplayReport.model_validate_json(
        saved.model_dump_json(exclude={"decision_trace"})
    )
    target = InspectionTarget(start_seconds=12, label="fool")
    context = inspect_context(legacy, target, cache_dir=cache_dir)
    assert context.evidence == "reconstructed"
    assert context.response is None and context.accepted is None and context.attempts == []
    assert [words[0].start for words in context.windows] == [10, 12, 14]
    path = next(cache_dir.glob("v1/*/*.json"))
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="has changed"):
        inspect_context(legacy, target, cache_dir=cache_dir)
    path.unlink()
    with pytest.raises(ValueError, match="unavailable"):
        inspect_context(legacy, target, cache_dir=cache_dir)


def test_follow_up_and_rerun_use_same_model_and_leave_history_unchanged(run_report, monkeypatch):
    run, cache_dir = run_report
    report = run(lambda prompt, **_: _detection_reply(prompt))
    before = report.model_dump_json()
    finding = report.findings[0]
    context = inspect_context(
        report,
        InspectionTarget(start_seconds=finding.start_seconds, label="fool"),
        cache_dir=cache_dir,
    )
    calls = []
    model_ids = []

    def generate(prompt, max_tokens):
        calls.append((prompt, max_tokens))
        if "User question:" in prompt:
            return json.dumps(
                {"answer": "The phrase is an insult.", "suggested_edit": "Require direct address."}
            )
        return _detection_reply(prompt, flag_fool=False)

    def load(model):
        model_ids.append(model)
        return generate

    monkeypatch.setattr(qwen_language, "load_language_generator", load)
    answer = ask_qwen(context, "Why did it flag this?")
    assert answer.suggested_edit == "Require direct address."
    assert calls[0][1] == 1024
    assert '"evidence": "recorded"' in calls[0][0] and '"accepted": true' in calls[0][0]
    assert "latest_test" not in calls[0][0]
    revised = context.instructions + "\n" + answer.suggested_edit
    experiment = rerun_inspection(context, revised)
    assert experiment.accepted and experiment.findings == []
    assert calls[1] == (revised + _JSON_INPUT + context.input_json, 2048)
    assert model_ids == [report.qwen_model, report.qwen_model]
    assert report.qwen_revision == manifest().artifact(report.qwen_model).revision
    assert report.model_dump_json() == before

    with pytest.raises(ValueError, match="revision differs"):
        rerun_inspection(context.model_copy(update={"qwen_revision": "bad"}), revised)
    assert len(model_ids) == 2


@pytest.mark.parametrize("accepted", [True, False])
def test_follow_up_prioritizes_exact_tested_instructions_and_validator_result(
    run_report, monkeypatch, accepted
):
    run, cache_dir = run_report
    report = run(lambda prompt, **_: _detection_reply(prompt))
    before = report.model_dump_json()
    finding = report.findings[0]
    context = inspect_context(
        report,
        InspectionTarget(start_seconds=finding.start_seconds, label=finding.label),
        cache_dir=cache_dir,
    )
    instructions = context.instructions + '\nUser question: "quoted draft text"  '
    asks = []

    def generate(prompt, max_tokens):
        if max_tokens == 1024:
            asks.append(prompt)
            return json.dumps({"answer": "The test applied the revised rule."})
        if not accepted:
            return "unreadable test response"
        return _detection_reply(prompt).replace('"severity": "low"', '"severity": "high"')

    monkeypatch.setattr(qwen_language, "load_language_generator", lambda _: generate)
    result = rerun_inspection(context, instructions)
    latest_test = InspectionTestEvidence(instructions=instructions, result=result)
    reply = ask_qwen(context, "What did the revised rule do?", latest_test=latest_test)
    assert reply.answer == "The test applied the revised rule."
    prompt, question = asks[0].split("\nUser question: ")
    guidance, evidence = prompt.split("\nEvidence: ")
    assert json.loads(question) == "What did the revised rule do?"
    assert "Use latest_test as the primary decision under review" in guidance
    assert "without a validated outcome" in guidance
    payload = json.loads(evidence)
    assert payload["saved_decision"]["finding"]["severity"] == "low"
    assert payload["saved_decision"]["inputs"] == context.inputs
    assert payload["latest_test"] == latest_test.model_dump(mode="json")
    assert payload["latest_test"]["result"]["accepted"] == accepted
    if accepted:
        assert payload["latest_test"]["result"]["findings"][0]["severity"] == "high"
    else:
        assert payload["latest_test"]["result"]["findings"] == []
    assert report.model_dump_json() == before


def test_classification_experiment_uses_its_original_validator(run_report, monkeypatch):
    run, cache_dir = run_report

    def generate(prompt, max_tokens):
        if max_tokens == 2048:
            return _detection_reply(prompt, flag_fool=False)
        return json.dumps(
            {
                "version": 2,
                "results": [
                    {"id": 0, "subcategory": "nameCalling", "severity": "high"},
                ],
            }
        )

    report = run(generate, ["fool"])
    finding = report.findings[0]
    context = inspect_context(
        report,
        InspectionTarget(start_seconds=finding.start_seconds, label="fool"),
        cache_dir=cache_dir,
    )
    assert context.kind == "classification" and context.max_tokens == 512
    assert context.inputs[0]["word"] == "fool"
    experiment = rerun_inspection(context, context.instructions)
    assert experiment.accepted and experiment.findings == [finding]
    monkeypatch.setattr(qwen_language, "load_language_generator", lambda _: lambda *_a, **_k: "bad")
    invalid = rerun_inspection(context, context.instructions)
    assert not invalid.accepted and invalid.response == "bad" and invalid.findings == []
