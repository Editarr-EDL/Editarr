"""Local prompt replay exercises the scanner with fixed transcript and model responses."""

import json
import shutil
import wave
from types import SimpleNamespace

import pytest

from scanner.commands.qwen_language_replay_cli import main
from scanner.evaluation.qwen_language_comparison import align_language_replay_findings
from scanner.model_acquisition import manifest
from scanner.models import FindingCreate
from scanner.pipeline import audio, qwen_language
from scanner.settings.capabilities import DEFAULT_QWEN_MODEL, QWEN35_9B_MODEL


@pytest.fixture
def replay_backend(monkeypatch):
    transcriptions = []
    prompts = []
    loaded_models = []
    template_options = []
    state = SimpleNamespace(
        transcriptions=transcriptions,
        prompts=prompts,
        loaded_models=loaded_models,
        template_options=template_options,
        response_valid=True,
    )

    def transcribe(_audio_path, model_name, cache_path, **_kwargs):
        transcriptions.append((model_name, cache_path))
        last_word_start = 1.1 if model_name == "whisper" else 1.2
        return [
            {
                "text": "You are a jerk",
                "start_seconds": 0.0,
                "end_seconds": 2.0,
                "words": [
                    {"word": "You", "start_seconds": 0.0, "end_seconds": 0.2},
                    {"word": " are", "start_seconds": 0.3, "end_seconds": 0.5},
                    {"word": " a", "start_seconds": 0.6, "end_seconds": 0.8},
                    {"word": " jerk", "start_seconds": last_word_start, "end_seconds": 1.6},
                ],
            }
        ]

    def generate(_model, _processor, prompt, **_kwargs):
        prompts.append(prompt)
        if not state.response_valid:
            return SimpleNamespace(text="invalid model output")
        severity = "high" if "Candidate prompt marker" in prompt else "low"
        inputs = json.loads(prompt.split("Inputs: ", 1)[1])
        if inputs and "word" in inputs[0]:
            classifications = [
                {"id": item["id"], "subcategory": "nameCalling", "severity": "medium"}
                for item in inputs
            ]
            return SimpleNamespace(text=json.dumps({"version": 2, "results": classifications}))
        match = {"first": 3, "last": 3, "subcategory": "nameCalling", "severity": severity}
        results = [{"id": item["id"], "matches": [match]} for item in inputs]
        return SimpleNamespace(text=json.dumps({"version": 1, "results": results}))

    def load_model(model):
        loaded_models.append(model)
        return None, None, None

    def apply_chat_template(_processor, _config, prompt, **options):
        template_options.append(options)
        return prompt

    monkeypatch.setattr(audio, "_transcribe_with_resumable_chunks", transcribe)
    monkeypatch.setattr(
        qwen_language,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available"),
    )
    monkeypatch.setattr(
        qwen_language.qwen_summary,
        "_require_dependency",
        lambda name, _hint: (
            SimpleNamespace(generate=generate)
            if name == "mlx_vlm"
            else SimpleNamespace(apply_chat_template=apply_chat_template)
        ),
    )
    monkeypatch.setattr(qwen_language.qwen_summary, "_load_model", load_model)
    return state


def _write_audio(path, sample=b"\0\0"):
    with wave.open(str(path), "wb") as audio_file:
        audio_file.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio_file.writeframes(sample * 32000)


def _run_args(audio_path, output, cache_dir, *options):
    return [
        "run",
        str(audio_path),
        "--output",
        str(output),
        "--cache-dir",
        str(cache_dir),
        "--audio-padding-seconds",
        "0.25",
        *options,
    ]


def test_prompt_replay_reuses_transcript_and_compares_findings(
    tmp_path, replay_backend, monkeypatch, capsys
):
    audio_path = tmp_path / "excerpt.wav"
    copied_audio = tmp_path / "renamed-excerpt.wav"
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    cache = tmp_path / "cache"
    _write_audio(audio_path)
    shutil.copyfile(audio_path, copied_audio)

    assert main(_run_args(audio_path, baseline, cache)) == 0
    monkeypatch.setattr(
        qwen_language,
        "_LANGUAGE_POLICY",
        qwen_language._LANGUAGE_POLICY + "Candidate prompt marker. ",
    )
    assert main(_run_args(copied_audio, candidate, cache)) == 0

    assert len(replay_backend.transcriptions) == 1
    assert len(replay_backend.prompts) == 2
    assert "Candidate prompt marker" in replay_backend.prompts[1]
    before, after = (json.loads(report.read_text()) for report in (baseline, candidate))
    assert before["status"] == after["status"] == "complete"
    assert before["audio_sha256"] == after["audio_sha256"]
    assert before["transcript_sha256"] == after["transcript_sha256"]
    assert len(before["findings"]) == len(after["findings"]) == 1
    [before_finding] = before["findings"]
    [after_finding] = after["findings"]
    assert before_finding["severity"] == "low"
    assert after_finding["severity"] == "high"
    assert after_finding["start_seconds"] == pytest.approx(0.85)
    assert after_finding["end_seconds"] == pytest.approx(1.85)

    capsys.readouterr()
    assert main(["compare", str(baseline), str(candidate)]) == 0
    comparison = capsys.readouterr().out
    assert all(value in comparison for value in ("jerk", "low", "high"))


def test_replay_compares_qwen35_to_default_with_the_same_transcript(
    tmp_path, replay_backend, capsys
):
    audio_path = tmp_path / "excerpt.wav"
    cache = tmp_path / "cache"
    baseline, candidate = tmp_path / "baseline.json", tmp_path / "candidate.json"
    _write_audio(audio_path)
    deny_options = ("--deny-word", "jerk")

    assert main(_run_args(audio_path, baseline, cache, *deny_options)) == 0
    assert (
        main(
            _run_args(audio_path, candidate, cache, "--qwen-model", QWEN35_9B_MODEL, *deny_options)
        )
        == 0
    )

    assert len(replay_backend.transcriptions) == 1
    assert replay_backend.loaded_models == [DEFAULT_QWEN_MODEL, QWEN35_9B_MODEL]
    assert (
        replay_backend.template_options
        == [{"num_images": 0}] * 2
        + [
            {"num_images": 0, "enable_thinking": False},
        ]
        * 2
    )

    before, after = (json.loads(report.read_text()) for report in (baseline, candidate))
    assert before["transcript_sha256"] == after["transcript_sha256"]
    assert (after["qwen_model"], after["qwen_revision"]) == (
        QWEN35_9B_MODEL,
        manifest().artifact(QWEN35_9B_MODEL).revision,
    )
    capsys.readouterr()
    assert main(["compare", str(baseline), str(candidate)]) == 0
    comparison = capsys.readouterr().out
    assert (
        f"Setting changed (qwen_model): {DEFAULT_QWEN_MODEL!r} → {QWEN35_9B_MODEL!r}" in comparison
    )


def test_replay_separates_transcript_cache_by_audio_contents_and_model(
    tmp_path, replay_backend, capsys
):
    audio_path = tmp_path / "excerpt.wav"
    cache = tmp_path / "cache"
    first, changed_audio, changed_model = (
        tmp_path / filename for filename in ("first.json", "second.json", "third.json")
    )
    _write_audio(audio_path)
    assert main(_run_args(audio_path, first, cache)) == 0
    _write_audio(audio_path, b"\x01\x00")
    assert main(_run_args(audio_path, changed_audio, cache)) == 0
    assert main(_run_args(audio_path, changed_model, cache, "--audio-model", "mlx_whisper")) == 0

    assert [model for model, _path in replay_backend.transcriptions] == [
        "whisper",
        "whisper",
        "mlx_whisper",
    ]
    assert len({path for _model, path in replay_backend.transcriptions}) == 3
    capsys.readouterr()
    assert main(["compare", str(first), str(changed_audio)]) != 0
    assert "different audio" in capsys.readouterr().err
    assert main(["compare", str(changed_audio), str(changed_model)]) != 0
    assert "different transcripts" in capsys.readouterr().err


def test_replay_reports_invalid_inference_as_failure(tmp_path, replay_backend, capsys):
    audio_path = tmp_path / "excerpt.wav"
    report = tmp_path / "invalid.json"
    _write_audio(audio_path)
    replay_backend.response_valid = False

    assert main(_run_args(audio_path, report, tmp_path / "cache")) != 0
    captured = capsys.readouterr()
    assert "failed" in captured.err.casefold()
    failed = json.loads(report.read_text())
    assert failed["status"] == "failed"
    assert failed["findings"] == []
    assert main(["compare", str(report), str(report)]) != 0


def test_replay_refuses_to_overwrite_source_audio(tmp_path, replay_backend, capsys):
    audio_path = tmp_path / "excerpt.wav"
    _write_audio(audio_path)
    expected = audio_path.read_bytes()

    assert main(_run_args(audio_path, audio_path, tmp_path / "cache")) != 0

    assert "output path must differ" in capsys.readouterr().err
    assert replay_backend.transcriptions == []
    assert audio_path.read_bytes() == expected


def test_replay_can_seed_a_finding_to_exercise_the_classification_prompt(tmp_path, replay_backend):
    audio_path = tmp_path / "excerpt.wav"
    report = tmp_path / "classified.json"
    _write_audio(audio_path)

    assert main(_run_args(audio_path, report, tmp_path / "cache", "--deny-word", "jerk")) == 0

    assert len(replay_backend.prompts) == 2
    classification_prompt, discovery_prompt = replay_backend.prompts
    assert json.loads(classification_prompt.split("Inputs: ", 1)[1]) == [
        {"id": 0, "word": "jerk", "context": "You are a jerk"}
    ]
    [window] = json.loads(discovery_prompt.split("Inputs: ", 1)[1])
    assert window["words"][3] == {"id": 3, "text": " jerk"}
    data = json.loads(report.read_text())
    assert data["deny_words"] == ["jerk"]
    [finding] = data["findings"]
    assert finding["subcategory"] == "nameCalling"
    assert finding["severity"] == "medium"


def test_replay_comparison_matches_the_closest_repeated_occurrence():
    def finding(start):
        return FindingCreate(
            video_id="fixture",
            category="language",
            subcategory="nameCalling",
            label="jerk",
            start_seconds=start,
            end_seconds=start + 0.5,
            source="audio",
        )

    earlier, later, candidate = (finding(start) for start in (2.10, 2.44, 2.40))
    rows = align_language_replay_findings(
        [later, earlier],
        [candidate],
        baseline_padding_seconds=0.2,
        candidate_padding_seconds=0.2,
    )
    assert [row.timestamp_seconds for row in rows] == pytest.approx([2.10, 2.40])
    assert [(row.baseline, row.candidate) for row in rows] == [(earlier, None), (later, candidate)]

    separate_rows = align_language_replay_findings(
        [earlier],
        [candidate],
        baseline_padding_seconds=0.2,
        candidate_padding_seconds=0.2,
    )
    assert [(row.baseline, row.candidate) for row in separate_rows] == [
        (earlier, None),
        (None, candidate),
    ]
