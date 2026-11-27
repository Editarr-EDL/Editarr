"""Tests for severity ground truth, description turns, d1 severity, cascade and describe-eval."""

from __future__ import annotations

import json
import math

import pytest

from scanner.evaluation.folder_eval.describe import (
    DESCRIBE_PROMPT,
    DESCRIBE_SCHEMA,
    clip_words,
    parse_description,
)
from scanner.evaluation.folder_eval.severity import (
    SeverityMap,
    quadratic_kappa,
    severity_metrics,
    window_severity,
)

SEVERITY = SeverityMap.load()


def _window(tags, expected=()):
    return {"id": "w", "window": [0, 10], "source_tags": list(tags), "expected": list(expected)}


def test_severity_is_max_over_tags_and_ambiguous_tags_unscore_the_key():
    levels = window_severity(
        _window(
            [
                "violence/non_graphic",
                "violence/graphic",
                "sexual/kissing_normal",
                "sexual/sex_implied",
                "drugs/drugs_legal",
                "language/profanity",
            ]
        ),
        None,
        SEVERITY,
    )
    assert levels == {"violence": 2, "sex": None, "nudity": 0, "drugs": 1}
    assert window_severity({"id": "clip"}, None, SEVERITY) is None
    both = window_severity(
        _window(["sexual/sex_with_nudity", "sexual/sexual_assault"]), None, SEVERITY
    )
    assert both["sex"] == 3 and both["nudity"] == 3


def test_corrections_zero_absent_keys_and_unscore_present_keys_without_tags():
    item = _window(["violence/graphic", "drugs/drugs_illegal"])
    levels = window_severity(
        item,
        {"violence": False, "fear/hemophobia": False, "sex/kissing": True, "drugs": True},
        SEVERITY,
    )
    assert levels == {"violence": 0, "sex": None, "nudity": 0, "drugs": 2}


def test_severity_metrics_and_quadratic_kappa():
    assert quadratic_kappa([(0, 0), (1, 1), (2, 2), (3, 3)], 4) == 1.0
    assert quadratic_kappa([(0, 3), (3, 0)], 4) < 0
    metrics = severity_metrics([0, 1, 2, None, 3], [0, 2, 2, 1, 0], 3)
    assert metrics["n"] == 4 and metrics["exact"] == 0.5 and metrics["within_1"] == 0.75
    assert severity_metrics([None], [1], 3) == {
        "n": 0,
        "exact": None,
        "within_1": None,
        "kappa": None,
    }


def test_parse_description_validates_clamps_and_clips():
    text = (
        'ok {"description": "A man punches another man.", "severity": '
        '{"violence": 5, "sex": 0, "nudity": 0, "drugs": 1}}'
    )
    assert parse_description(text) == (
        "A man punches another man.",
        {"violence": 3, "sex": 0, "nudity": 0, "drugs": 1},
    )
    for bad in (
        "no json",
        '{"description": "x"}',
        '{"description": "x", "severity": {"violence": "high", "sex": 0, "nudity": 0, "drugs": 0}}',
    ):
        with pytest.raises(ValueError):
            parse_description(bad)
    assert clip_words("a " * 70).endswith("…") and len(clip_words("a " * 70).split()) == 60
    assert DESCRIBE_SCHEMA["properties"]["severity"]["properties"]["drugs"]["enum"] == [0, 1, 2]
    assert "3 = gore" in DESCRIBE_PROMPT and "2 = illegal drugs" in DESCRIBE_PROMPT


class _DescServer:
    """llama-server fake: yes/no turns use `p_yes`, the JSON turn returns `reply`."""

    def __init__(self, p_yes, reply):
        self.p_yes, self.reply, self.bodies = p_yes, reply, []

    def ensure_started(self):
        pass

    def sample_rss(self):
        pass

    def get(self, path):
        return {"media_marker": "<m>"}

    def post(self, path, body):
        self.bodies.append(body)
        if "json_schema" in body:
            return {"content": self.reply}
        return {
            "content": "no",
            "completion_probabilities": [
                {
                    "top_logprobs": [
                        {"token": "yes", "logprob": math.log(self.p_yes)},
                        {"token": "no", "logprob": math.log(1 - self.p_yes)},
                    ]
                }
            ],
        }


REPLY = (
    '{"description": "A fist fight.", "severity": {"violence": 1, "sex": 0, "nudity": 0, '
    '"drugs": 0}}'
)


@pytest.mark.parametrize(
    ("model_id", "p_yes", "status", "description"),
    [
        ("qwen3vl-8b-desc", 0.1, "ok", "A fist fight."),
        ("qwen3vl-8b-desc-cond", 0.9, "ok", "A fist fight."),
        ("qwen3vl-8b-desc-cond", 0.1, "skipped", "none"),
    ],
)
def test_llama_cpp_description_turn(monkeypatch, tmp_path, model_id, p_yes, status, description):
    from scanner.evaluation.folder_eval.decision.qwen_vlm import SPECS, QwenVlmBackend

    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    backend = QwenVlmBackend(SPECS[model_id], models_dir=tmp_path)
    monkeypatch.setattr(backend, "check_available", lambda: None)
    backend._server = _DescServer(p_yes, REPLY)
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")

    (result,) = backend.score_frames([frame])

    assert result["describe"] == status and result["description"] == description
    bodies = backend._server.bodies
    if status == "skipped":
        assert len(bodies) == 5 and result["severity"]["violence"] == 0
    else:
        assert len(bodies) == 6 and bodies[-1]["n_predict"] == 120
        prompt = bodies[-1]["prompt"]["prompt_string"]
        assert prompt.startswith(bodies[-2]["prompt"]["prompt_string"])
        assert DESCRIBE_PROMPT in prompt and result["severity"]["violence"] == 1


def test_llama_cpp_invalid_json_is_a_failure(monkeypatch, tmp_path):
    from scanner.evaluation.folder_eval.decision.qwen_vlm import SPECS, QwenVlmBackend

    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    backend = QwenVlmBackend(SPECS["qwen3vl-8b-desc"], models_dir=tmp_path)
    monkeypatch.setattr(backend, "check_available", lambda: None)
    backend._server = _DescServer(0.1, '{"description": ')
    (tmp_path / "f.jpg").write_bytes(b"x")

    (result,) = backend.score_frames([tmp_path / "f.jpg"])

    assert result["describe"] == "failed" and result["description"] is None


class _MlxRuntime:
    """Mocked MLX runtime: yes/no answers P(yes)=p; JSON turns emit `replies` in order."""

    def __init__(self, p_yes, replies):
        from scanner.evaluation.folder_eval.decision.qwen_mlx import Step

        self.Step, self.p_yes, self.replies, self.calls = Step, p_yes, list(replies), []
        self.pending: list[int] = []

    def _yes_no(self):
        return self.Step(
            1,
            False,
            [
                {"token": "Yes", "logprob": math.log(self.p_yes)},
                {"token": "No", "logprob": math.log(1 - self.p_yes)},
            ],
        )

    def begin(self, prompt, image):
        self.calls.append(("begin", prompt))
        return self._yes_no()

    def extend(self, text):
        self.calls.append(("extend", text))
        if "Answer yes or no." in text:
            return self._yes_no()
        self.pending = [2, 3]
        return self.Step(2, False, [])

    def push(self, token_id):
        if self.pending:
            self.pending.pop(0)
        return self.Step(3, not self.pending, [])

    def decode(self, ids):
        return self.replies.pop(0)

    def close(self):
        pass


def test_mlx_description_retries_once_then_fails(tmp_path):
    from scanner.evaluation.folder_eval.decision.chat import vlm_questions
    from scanner.evaluation.folder_eval.decision.qwen_mlx import RETRY_PROMPT, SPECS, converse

    spec = SPECS["qwen3vl-8b-mlx-desc"]
    ok = converse(_MlxRuntime(0.2, ["not json", REPLY]), spec, tmp_path / "f.jpg", vlm_questions())
    assert ok["describe"] == "retried" and ok["severity"]["violence"] == 1

    runtime = _MlxRuntime(0.2, ["bad", "worse"])
    failed = converse(runtime, spec, tmp_path / "f.jpg", vlm_questions())
    assert failed["describe"] == "failed" and failed["description"] is None
    assert any(RETRY_PROMPT in call[1] for call in runtime.calls)

    skipped = converse(
        _MlxRuntime(0.2, []), SPECS["qwen3vl-8b-mlx-desc-cond"], tmp_path / "f.jpg", vlm_questions()
    )
    assert skipped["describe"] == "skipped" and skipped["description"] == "none"


def test_d1_severity_request_and_parsing(monkeypatch, tmp_path):
    from scanner.evaluation.folder_eval.decision.d1_severity import (
        parse_severity,
        severity_questions,
    )
    from scanner.evaluation.folder_eval.decision.registry import BACKENDS

    questions = severity_questions()
    assert questions["sev_violence"]["type"] == "score"
    assert questions["sev_violence"]["criteria"][3].startswith("Gore")
    assert len(questions["sev_drugs"]["criteria"]) == 3
    body = {
        "answers": {
            "sev_violence": {"probabilities": [0.1, 0.2, 0.6, 0.1]},
            "sev_sex": {"probabilities": {"0": 0.7, "1": 0.1, "2": 0.1, "3": 0.1}},
            "sev_nudity": {"probabilities": [1, 0, 0, 0]},
            "sev_drugs": {"probabilities": [0.2, 0.3, 0.5]},
        }
    }
    levels, expected = parse_severity(body)
    assert levels == {"violence": 2, "sex": 0, "nudity": 0, "drugs": 2}
    assert expected["violence"] == pytest.approx(1.7)
    with pytest.raises(ValueError):
        parse_severity({"answers": {}})

    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    backend = BACKENDS["d1-3b-sev"]()
    sent = []

    def post(request):
        sent.append(request)
        answers = {
            qid: {"noul": 0.8} for qid, q in request["questions"].items() if q["type"] == "noul"
        }
        answers.update(body["answers"])
        return {"answers": answers}

    monkeypatch.setattr(backend, "_ensure_server", lambda: None)
    monkeypatch.setattr(backend, "_post", post)
    (tmp_path / "f.jpg").write_bytes(b"x")
    (result,) = backend.score_frames([tmp_path / "f.jpg"])
    assert len(sent) == 1 and len(sent[0]["questions"]) == 8
    assert result["scores"]["violence"] == 0.8 and result["severity"]["violence"] == 2


def test_cascade_runs_decider_then_describer_on_flagged_frames_only(tmp_path):
    from scanner.evaluation.folder_eval.decision.cascade import CascadeBackend

    events = []

    class Decider:
        default_concurrency = 2

        def score_frames(self, paths):
            hot = paths[0].name == "hot.jpg"
            return [{"scores": {"violence": 0.9 if hot else 0.1}, "severity": {"violence": 2}}]

        def close(self):
            events.append("decider closed")

    class Describer:
        def __init__(self):
            events.append("describer loaded")

        def score_frames(self, paths):
            events.append(f"describe {paths[0].name}")
            return [{"scores": {}, "transcript": ["Two men fight. " * 40]}]

        def close(self):
            events.append("describer closed")

    cascade = CascadeBackend(decider=Decider, describer=Describer)
    outputs = cascade.score_items([[tmp_path / "hot.jpg"], [tmp_path / "calm.jpg"]])

    assert events == ["decider closed", "describer loaded", "describe hot.jpg", "describer closed"]
    hot, calm = outputs[0][0], outputs[1][0]
    assert hot["describe"] == "ok" and len(hot["description"].split()) == 60
    assert hot["severity"] == {"violence": 2}
    assert calm["description"] == "none" and calm["describe"] == "skipped"


def test_runner_hands_batch_backends_every_pending_item(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval import runner
    from scanner.evaluation.folder_eval.paths import Layout

    layout = Layout(tmp_path)
    items = []
    for name in ("a", "b"):
        items.append(
            {
                "id": name,
                "path": f"films/F/{name}.mkv",
                "folder": "films/F",
                "window": [0, 10],
                "expected": [],
                "scored": True,
                "kind": "video",
                "frames": [{"n": 0, "t": 0, "file": f"frames/{name}/0.jpg"}],
            }
        )
        (layout.results / "frames" / name).mkdir(parents=True)
        (layout.results / "frames" / name / "0.jpg").write_bytes(b"x")
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")
    layout.models.mkdir()
    calls = []

    class Batch:
        model_id = "batch"
        default_concurrency = 1

        def meta(self):
            return {"supports": ["violence"], "thresholds": {"*": 0.5}}

        def score_items(self, batch):
            calls.append(len(batch))
            return [
                [
                    {
                        "scores": {"violence": 0.7},
                        "description": "A fight.",
                        "severity": {"violence": 1},
                        "describe": "ok",
                    }
                ]
                for _ in batch
            ]

        def close(self):
            pass

    monkeypatch.setattr(runner, "create_backend", lambda _model_id: Batch())
    runner.run_model(layout, "batch")

    text = layout.model_jsonl("batch").read_text()
    lines = [json.loads(line) for line in text.splitlines() if line]
    assert calls == [2] and len(lines) == 2
    frame = lines[0]["frames"][0]
    assert frame["description"] == "A fight." and frame["severity"] == {"violence": 1}
    assert lines[0]["flagged"] == ["violence"]


def test_evaluate_model_counts_hits_hallucinations_and_failures():
    from scanner.evaluation.folder_eval.describe_eval import MENTION_KEYS, evaluate_model

    class FakeJudge:
        def mentions(self, text):
            return {key: float(key in text) for key in MENTION_KEYS}

    def row(expected, description, status="ok", severity=None, truth=None):
        frame = {"description": description, "describe": status}
        if severity is not None:
            frame["severity"] = severity
        return {"expected": expected, "truth_severity": truth, "frame": frame}

    sev = {"violence": 2, "sex": 0, "nudity": 0, "drugs": 0}
    rows = [
        row(["violence"], "violence", severity=sev, truth=sev),
        row(["violence"], "none", status="skipped", severity=dict.fromkeys(sev, 0), truth=sev),
        row([], "violence and drugs", severity=sev, truth=dict.fromkeys(sev, 0)),
        row([], None, status="failed"),
    ]

    result = evaluate_model(rows, FakeJudge())

    violence = result["description"]["violence"]
    assert (violence["hits"], violence["hallucinations"]) == (1, 1)
    assert violence["recall"] == 0.5 and violence["hallucination_rate"] == 0.5
    assert result["description"]["drugs"]["hallucinations"] == 1
    assert result["none_on_positive_windows"] == 0.5
    assert result["json_failure_rate"] == round(1 / 3, 4) and result["describe_skipped"] == 1
    assert result["severity"]["violence"]["n"] == 3
    assert result["severity"]["violence"]["exact"] == round(1 / 3, 4)
