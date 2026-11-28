"""Tests for the one-turn JSON VLM ids (decisions, severity and description in one reply)."""

from __future__ import annotations

import math

import pytest


def _steps(text_tokens, alternatives):
    """Generation steps; `alternatives` maps a token index to its top log-probabilities."""
    return [
        (token, alternatives.get(i, [{"token": token, "logprob": 0.0}]))
        for i, token in enumerate(text_tokens)
    ]


ONEJSON_TOKENS = [
    '{"',
    "drugs",
    '":',
    " false",
    ', "',
    "nudity",
    '":',
    " false",
    ', "',
    "sex",
    '":',
    " false",
    ', "',
    "violence",
    '":',
    " true",
    ', "',
    "blood",
    "_g",
    "ore",
    '":',
    " false",
    ', "severity": {"violence": 1, "sex": 0, "nudity": 0, ',
    '"drugs": 0}, "description": "A fist fight."}',
]


def test_boolean_probabilities_read_the_value_token_and_record_fallbacks():
    from scanner.evaluation.folder_eval.decision.qwen_onejson import frame_from_reply

    alternatives = {
        15: [
            {"token": " true", "logprob": math.log(0.8)},
            {"token": " false", "logprob": math.log(0.2)},
        ],
        3: [
            {"token": " false", "logprob": math.log(0.9)},
            {"token": "False", "logprob": math.log(0.05)},
            {"token": " true", "logprob": math.log(0.05)},
        ],
    }
    frame = frame_from_reply(_steps(ONEJSON_TOKENS, alternatives))

    assert frame["scores"]["violence"] == pytest.approx(0.8)
    assert frame["scores"]["drugs"] == pytest.approx(0.05)
    assert frame["scores"]["fear/hemophobia"] == 0.0
    assert frame["logprob_fallback"] == ["fear/hemophobia", "nudity", "sex"]
    assert frame["severity"]["violence"] == 1 and frame["description"] == "A fist fight."
    assert frame["describe"] == "ok"

    broken = frame_from_reply(_steps(ONEJSON_TOKENS[:16], alternatives))
    assert broken["describe"] == "failed" and broken["scores"]["violence"] == pytest.approx(0.8)
    assert broken["scores"]["fear/hemophobia"] == 0.0  # missing key scored 0


def test_onejson_schema_orders_fields_and_prompt_follows_it():
    from scanner.evaluation.folder_eval.decision.qwen_onejson import onejson_prompt, onejson_schema

    first = onejson_schema("decide-first")
    assert list(first["properties"]) == [
        "drugs",
        "nudity",
        "sex",
        "violence",
        "blood_gore",
        "severity",
        "description",
    ]
    assert first["required"] == list(first["properties"])
    described = onejson_schema("describe-first")
    assert list(described["properties"])[0] == "description"
    prompt = onejson_prompt("describe-first")
    assert prompt.index('"description"') < prompt.index('"drugs"') < prompt.index('"severity"')
    assert "blood or gore" in prompt and "3 = gore" in prompt


def test_llama_cpp_onejson_sends_one_grammar_constrained_request(monkeypatch, tmp_path):
    from scanner.evaluation.folder_eval.decision.registry import BACKENDS

    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    backend = BACKENDS["qwen3vl-8b-onejson-descfirst"]()
    sent = []

    class Server:
        def ensure_started(self):
            pass

        def sample_rss(self):
            pass

        def get(self, path):
            return {"media_marker": "<m>"}

        def post(self, path, body):
            sent.append(body)
            return {
                "completion_probabilities": [
                    {"token": token, "top_logprobs": top}
                    for token, top in _steps(ONEJSON_TOKENS, {})
                ]
            }

    monkeypatch.setattr(backend, "check_available", lambda: None)
    backend._server = Server()
    (tmp_path / "f.jpg").write_bytes(b"x")

    (frame,) = backend.score_frames([tmp_path / "f.jpg"])

    assert len(sent) == 1 and sent[0]["n_probs"] == 20 and sent[0]["n_predict"] == 160
    assert list(sent[0]["json_schema"]["properties"])[0] == "description"
    assert sent[0]["prompt"]["prompt_string"].endswith("<|im_start|>assistant\n")
    assert frame["scores"]["violence"] == 1.0 and len(frame["logprob_fallback"]) == 5


def test_mlx_onejson_retries_once_in_the_conversation(tmp_path):
    from scanner.evaluation.folder_eval.decision.onejson_backends import converse_onejson
    from scanner.evaluation.folder_eval.decision.qwen_mlx import RETRY_PROMPT, SPECS, Step

    class Runtime:
        def __init__(self, replies):
            self.replies, self.calls, self.queue = list(replies), [], []

        def _start(self):
            self.queue = list(self.replies.pop(0))
            return Step(0, False, [])

        def begin(self, prompt, image):
            self.calls.append(("begin", prompt))
            return self._start()

        def extend(self, text):
            self.calls.append(("extend", text))
            return self._start()

        def push(self, token_id):
            self.queue.pop(0)
            return Step(0, not self.queue, [])

        def decode(self, ids):
            return self.queue[0]

    spec = SPECS["qwen3vl-8b-mlx"]
    runtime = Runtime([["not json"], ONEJSON_TOKENS])
    frame = converse_onejson(runtime, spec, tmp_path / "f.jpg", "decide-first")
    assert frame["describe"] == "retried" and frame["severity"]["violence"] == 1
    assert RETRY_PROMPT in runtime.calls[1][1]
    failed = converse_onejson(Runtime([["x"], ["y"]]), spec, tmp_path / "f.jpg", "decide-first")
    assert failed["describe"] == "failed"
