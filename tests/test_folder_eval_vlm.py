"""Tests for the generative VLM backends, the film subset and generic subset summaries."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scanner.evaluation.folder_eval.decision.chat import (
    ASSISTANT,
    TRANSCRIPT_LIMIT,
    first_turn,
    next_turn,
    vlm_questions,
    yes_probability,
)
from scanner.evaluation.folder_eval.decision.qwen_vlm import SPECS, QwenVlmBackend
from scanner.evaluation.folder_eval.paths import Layout
from scanner.evaluation.folder_eval.subsets import build_film_subset


def _logprob(probability: float) -> float:
    return math.log(probability)


def test_yes_probability_normalises_yes_and_no_variants():
    top = [
        {"token": "No", "logprob": _logprob(0.5)},
        {"token": " yes", "logprob": _logprob(0.2)},
        {"token": "Yes", "logprob": _logprob(0.1)},
        {"token": "no.", "logprob": _logprob(0.1)},
        {"token": "Maybe", "logprob": _logprob(0.1)},
    ]

    assert yes_probability(top) == pytest.approx(0.3 / 0.9)
    assert yes_probability([{"token": "The", "logprob": -0.1}]) == 0.0


def test_questions_reuse_decision_wording_and_support_exactly_five_keys():
    questions = vlm_questions()

    assert [question.key for question in questions] == [
        "drugs",
        "nudity",
        "sex",
        "violence",
        "fear/hemophobia",
    ]
    assert questions[3].text == "Does this video frame show violence?"
    assert questions[4].text == "Does this frame show blood or gore?"
    meta = QwenVlmBackend(SPECS["qwen38-9b"]).meta()
    assert meta["supports"] == ["drugs", "fear/hemophobia", "nudity", "sex", "violence"]


def test_turns_disable_thinking_and_extend_the_previous_prompt():
    first = first_turn("<m>", "Q1")
    assert first == "<|im_start|>user\n<m>Q1<|im_end|>\n" + ASSISTANT
    assert ASSISTANT.endswith("<think>\n\n</think>\n\n")
    assert next_turn("no", "Q2") == "no<|im_end|>\n<|im_start|>user\nQ2<|im_end|>\n" + ASSISTANT


class _FakeServer:
    """Answers /completion: generation turns get long text, yes/no turns a fixed P(yes)."""

    def __init__(self, p_yes: dict[str, float]):
        self.p_yes = p_yes
        self.prompts: list[str] = []

    def ensure_started(self):
        pass

    def sample_rss(self):
        pass

    def get(self, path):
        assert path == "/props"
        return {"media_marker": "<__media__>"}

    def post(self, path, body):
        assert path == "/completion" and body["temperature"] == 0 and body["cache_prompt"]
        prompt = body["prompt"]["prompt_string"]
        assert prompt.count("<__media__>") == 1 and len(body["prompt"]["multimodal_data"]) == 1
        self.prompts.append(prompt)
        if body["n_predict"] > 1:
            return {"content": "x" * 3000}
        question = prompt.rsplit("<|im_start|>user\n", 1)[1]
        p = next((v for k, v in self.p_yes.items() if k in question), 0.1)
        return {
            "content": "yes" if p >= 0.5 else "no",
            "completion_probabilities": [
                {
                    "top_logprobs": [
                        {"token": "yes", "logprob": _logprob(p)},
                        {"token": "no", "logprob": _logprob(1 - p)},
                    ]
                }
            ],
        }


def _backend(monkeypatch, tmp_path, model_id, p_yes):
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    backend = QwenVlmBackend(SPECS[model_id], models_dir=tmp_path)
    monkeypatch.setattr(backend, "check_available", lambda: None)
    backend._server = _FakeServer(p_yes)
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"jpeg")
    return backend, frame


def test_direct_backend_asks_five_chained_questions(monkeypatch, tmp_path):
    backend, frame = _backend(monkeypatch, tmp_path, "qwen38-9b", {"violence?": 0.8})

    (result,) = backend.score_frames([frame])

    prompts = backend._server.prompts
    assert len(prompts) == 5
    assert all(later.startswith(earlier) for earlier, later in zip(prompts, prompts[1:]))
    assert "yes<|im_end|>" in prompts[4] and prompts[4].endswith(ASSISTANT)
    assert result["scores"]["violence"] == pytest.approx(0.8)
    assert result["scores"]["drugs"] == pytest.approx(0.1)
    assert result["asked"] == sorted(result["scores"]) and "transcript" not in result


def test_seq_backend_runs_four_turns_then_questions_and_keeps_transcript(monkeypatch, tmp_path):
    backend, frame = _backend(monkeypatch, tmp_path, "qwen38-9b-seq", {"gore?": 0.7})

    (result,) = backend.score_frames([frame])

    prompts = backend._server.prompts
    assert len(prompts) == 9
    assert all(later.startswith(earlier) for earlier, later in zip(prompts, prompts[1:]))
    assert "Locate all sensitive objects with bounding boxes." in prompts[0]
    assert prompts[5].count("x" * 3000) == 4  # full replies stay in the conversation
    assert [len(text) for text in result["transcript"]] == [TRANSCRIPT_LIMIT] * 4
    assert result["scores"]["fear/hemophobia"] == pytest.approx(0.7)


def test_vlm_ids_are_registered_and_use_the_bundled_runtime(monkeypatch, tmp_path):
    from scanner.evaluation.folder_eval.decision.registry import BACKENDS

    monkeypatch.delenv("EDITARR_LLAMA_SERVER", raising=False)
    binary = tmp_path / "llama.cpp-b12556" / "llama-b12556" / "llama-server"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    binary.chmod(0o755)

    assert {"qwen38-9b", "qwen38-9b-seq"} <= set(BACKENDS)
    command = QwenVlmBackend(SPECS["qwen38-9b"], models_dir=tmp_path).server_command(1)
    assert command[0] == str(binary)
    assert command[command.index("--mmproj") + 1].endswith("qwen38-9b/mmproj-F16.gguf")


def _window(identifier, expected):
    return {
        "id": identifier,
        "path": "films/F/f.mkv",
        "folder": "films/F",
        "window": [0, 10],
        "expected": expected,
        "scored": True,
    }


def test_film_subset_is_deterministic_with_minimum_positives_per_key():
    items = [_window(f"c{i:03d}", []) for i in range(200)]
    items += [_window(f"d{i}", ["drugs", "drugs/alcohol"]) for i in range(5)]
    items += [_window(f"v{i:02d}", ["violence", "fear", "fear/hemophobia"]) for i in range(40)]
    items += [_window(f"s{i:02d}", ["sex", "nudity"]) for i in range(20)]
    items.append(
        {
            "id": "clip",
            "path": "video/x/a.mp4",
            "folder": "video/x",
            "expected": ["drugs"],
            "scored": True,
        }
    )
    base = {item["id"]: [0] for item in items}

    subset = build_film_subset(items, base, size=60, seed=2026, min_per_key=10)

    ids = [entry["id"] for entry in subset]
    assert subset == build_film_subset(items, base, size=60, seed=2026, min_per_key=10)
    assert len(ids) == 60 and len(set(ids)) == 60 and "clip" not in ids
    assert sum(identifier.startswith("d") for identifier in ids) == 5  # all available
    assert sum(identifier.startswith("v") for identifier in ids) >= 10
    assert sum(identifier.startswith("s") for identifier in ids) >= 10
    assert any(identifier.startswith("c") for identifier in ids)
    assert all(entry["frames"] == [0] for entry in subset)


def test_summary_has_a_section_per_subset_for_models_with_results(tmp_path):
    from scanner.evaluation.folder_eval.summary import build_summary

    layout = Layout(tmp_path)
    items = [
        {**_window("w1", ["violence"]), "kind": "video", "frames": []},
        {**_window("w2", []), "kind": "video", "frames": []},
    ]
    layout.results.mkdir(parents=True)
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")
    (layout.results / "subsets").mkdir()
    for name, ids in (("decision_sample", ["w1", "w2"]), ("films_x", ["w1"])):
        payload = {"items": [{"id": identifier, "frames": [0]} for identifier in ids]}
        (layout.results / "subsets" / f"{name}.json").write_text(json.dumps(payload))
    layout.models.mkdir()
    line = {"id": "w1", "status": "ok", "frames": [{"n": 0, "scores": {"violence": 0.9}}]}
    layout.model_jsonl("with").write_text(json.dumps(line) + "\n", encoding="utf-8")
    layout.model_jsonl("without").write_text("", encoding="utf-8")
    for model in ("with", "without"):
        layout.model_meta(model).write_text(json.dumps({"supports": ["violence"]}))

    summary = build_summary(layout, {})

    assert set(summary["subsets"]) == {"decision_sample", "films_x"}
    assert set(summary["models"]["with"]["subsets"]) == {"decision_sample", "films_x"}
    assert set(summary["models"]["without"]["subsets"]) == {"decision_sample"}
    films = summary["models"]["with"]["subsets"]["films_x"]["films"]["all"]
    assert films["families"]["violence"]["tp"] == 1


def test_run_keeps_frame_transcripts(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval import runner

    layout = Layout(tmp_path)
    item = {
        **_window("w1", []),
        "kind": "video",
        "frames": [{"n": 0, "t": 0, "file": "frames/w1/0.jpg"}],
    }
    (layout.results / "frames" / "w1").mkdir(parents=True)
    (layout.results / "frames" / "w1" / "0.jpg").write_bytes(b"x")
    layout.inventory.write_text(json.dumps({"items": [item]}), encoding="utf-8")
    layout.models.mkdir()

    class Fake:
        model_id = "fake"
        default_concurrency = 1

        def meta(self):
            return {"supports": ["violence"], "thresholds": {"*": 0.5}}

        def score_frames(self, paths):
            return [{"scores": {"violence": 0.1}, "transcript": ["a", "b"]} for _ in paths]

        def close(self):
            pass

    monkeypatch.setattr(runner, "create_backend", lambda _model_id: Fake())
    runner.run_model(layout, "fake")

    line = json.loads(Path(layout.model_jsonl("fake")).read_text().splitlines()[0])
    assert line["frames"][0]["transcript"] == ["a", "b"]


@pytest.mark.parametrize(
    ("base_id", "model_file", "thinking"),
    [
        ("qwen38-9b", "Qwen3.8-9B-Q8_0.gguf", True),
        ("qwen3vl-8b", "Qwen3VL-8B-Instruct-Q8_0.gguf", False),
        ("qwen38-27b", "Qwen3.8-27B-UD-IQ4_XS.gguf", True),
    ],
)
def test_each_model_has_direct_and_seq_ids_sharing_weights(base_id, model_file, thinking):
    from scanner.evaluation.folder_eval.decision.registry import BACKENDS

    direct, seq = SPECS[base_id], SPECS[f"{base_id}-seq"]

    assert {base_id, f"{base_id}-seq"} <= set(BACKENDS)
    assert direct.weights_id == seq.weights_id == base_id
    assert direct.model_file == seq.model_file == model_file
    assert direct.turns == () and len(seq.turns) == 4
    assert (direct.assistant == ASSISTANT) is thinking
    assert direct.assistant.startswith("<|im_start|>assistant\n")


def test_instruct_model_prompts_have_no_think_block(monkeypatch, tmp_path):
    backend, frame = _backend(monkeypatch, tmp_path, "qwen3vl-8b", {})

    backend.score_frames([frame])

    prompts = backend._server.prompts
    assert all("<think>" not in prompt for prompt in prompts)
    assert prompts[0].endswith("Answer yes or no.<|im_end|>\n<|im_start|>assistant\n")
