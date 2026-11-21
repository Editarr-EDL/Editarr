"""Qwen3-VL and d1-3B video detectors with mocked models (no weights)."""

import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from scanner.models import McfCategoryFamily, Severity
from scanner.pipeline import d1_detector, decision_detection, qwen_detector, visual
from scanner.pipeline.decision_detection import DecisionDetector, FrameDecision
from scanner.settings.config import settings

F = McfCategoryFamily
QUESTIONS = {
    F.DRUGS: "Does this video frame show drugs, alcohol, smoking, or gambling?",
    F.NUDITY: "Does this video frame show nudity?",
    F.SEX: "Does this video frame show sexual content?",
    F.VIOLENCE: "Does this video frame show violence?",
}


def test_video_model_ids_are_supported():
    for video_model in ("qwen3-vl-8b", "qwen3-vl-4b", "d1-3b"):
        assert visual.normalize_video_model(video_model) == video_model


def test_yes_probability_normalizes_yes_and_no_candidates():
    top = [("Yes", math.log(0.3)), (" no", math.log(0.1)), ("NO.", math.log(0.1)), ("x", 0.0)]

    assert qwen_detector.yes_probability(top) == pytest.approx(0.6)
    assert qwen_detector.yes_probability([("maybe", 0.0)]) == 0.0


class _Conversation:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def _step(self):
        yes = next(self.answers)
        return qwen_detector.Step(
            token_id=1 if yes >= 0.5 else 2,
            top=[("Yes", math.log(yes)), ("No", math.log(1 - yes))],
        )

    def begin(self, prompt, image):
        self.calls.append(("begin", prompt, image))
        return self._step()

    def extend(self, text):
        self.calls.append(("extend", text))
        return self._step()

    def push(self, token_id):
        self.calls.append(("push", token_id))

    def end(self):
        self.calls.append(("end",))


def test_qwen_chains_the_evaluated_questions_in_one_conversation(tmp_path):
    conversation = _Conversation([0.1, 0.8, 0.2, 0.6])
    frame = tmp_path / "frame.jpg"

    scores = qwen_detector.converse(conversation, frame)

    assert scores == pytest.approx({F.DRUGS: 0.1, F.NUDITY: 0.8, F.SEX: 0.2, F.VIOLENCE: 0.6})
    assert conversation.calls == [
        (
            "begin",
            "<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>"
            f"{QUESTIONS[F.DRUGS]} Answer yes or no.<|im_end|>\n<|im_start|>assistant\n",
            frame,
        ),
        ("push", 2),
        ("extend", qwen_detector.next_turn(f"{QUESTIONS[F.NUDITY]} Answer yes or no.")),
        ("push", 1),
        ("extend", qwen_detector.next_turn(f"{QUESTIONS[F.SEX]} Answer yes or no.")),
        ("push", 2),
        ("extend", qwen_detector.next_turn(f"{QUESTIONS[F.VIOLENCE]} Answer yes or no.")),
        ("end",),
    ]
    assert qwen_detector.next_turn("Q") == (
        "<|im_end|>\n<|im_start|>user\nQ<|im_end|>\n<|im_start|>assistant\n"
    )


@pytest.mark.parametrize(
    ("video_model", "artifact"),
    [("qwen3-vl-8b", "qwen3-vl-8b-instruct-4bit"), ("qwen3-vl-4b", "qwen3-vl-4b-instruct-4bit")],
)
def test_qwen_detector_shares_the_description_model_loader(monkeypatch, video_model, artifact):
    loaded = []
    model, processor = object(), object()
    monkeypatch.setattr(qwen_detector, "require_available", lambda _backend: None)
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary._load_model",
        lambda artifact_id: loaded.append(artifact_id) or (model, processor, object()),
    )
    monkeypatch.setattr(qwen_detector, "QwenConversation", lambda *args: args)

    assert qwen_detector._conversation(video_model) == (model, processor)
    assert loaded == [artifact]
    assert qwen_detector.detector(video_model).model_revision == (
        "defcdea7cc7a4b0858fea563cbbce171d328e457"
        if video_model == "qwen3-vl-8b"
        else "2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b"
    )


def test_d1_asks_family_decisions_then_rubric_severity_questions():
    questions = d1_detector.questions()

    assert list(questions) == [
        "drugs", "nudity", "sex", "violence",
        "sev_violence", "sev_sex", "sev_nudity", "sev_drugs",
    ]
    assert [questions[family.value]["instructions"] for family in QUESTIONS] == list(
        QUESTIONS.values()
    )
    assert {question["type"] for name, question in questions.items() if "sev_" not in name} == {
        "noul"
    }
    assert questions["sev_drugs"] == {
        "type": "score",
        "instructions": "How severe is the drug, alcohol or tobacco use in this video frame?",
        "criteria": [
            "No drugs, alcohol or smoking",
            "Legal drugs: alcohol, tobacco or medication",
            "Illegal drugs",
        ],
    }
    assert len(questions["sev_violence"]["criteria"]) == 4


def _d1_response(noul, levels):
    answers = {family: {"type": "noul", "noul": value} for family, value in noul.items()}
    for family, level in levels.items():
        size = 3 if family == "drugs" else 4
        answers[f"sev_{family}"] = {
            "type": "score",
            "probabilities": {str(i): 0.7 if i == level else 0.1 for i in range(size)},
        }
    return {"answers": answers}


def test_d1_decisions_use_detector_severity_and_violence_threshold():
    response = _d1_response(
        {"drugs": 0.49, "nudity": 0.9, "sex": 0.5, "violence": 0.26},
        {"violence": 2, "sex": 0, "nudity": 3, "drugs": 1},
    )

    decision = d1_detector.parse_answers(response)
    detections = decision_detection.detections_for_decision(decision, 4.0, d1_detector.DETECTOR)

    assert decision.positives() == (F.NUDITY, F.SEX, F.VIOLENCE)
    assert {
        (item.category, item.severity, item.severity_source, item.label, item.subcategory)
        for item in detections
    } == {
        (F.NUDITY, Severity.HIGH, "detector", "d1_nudity", "nudity"),
        # A positive decision whose argmax level is 0 is still reported as low.
        (F.SEX, Severity.LOW, "detector", "d1_sex", "sex"),
        (F.VIOLENCE, Severity.MEDIUM, "detector", "d1_violence", "violence"),
    }


def test_d1_scores_a_frame_in_one_system_one_call(monkeypatch, tmp_path):
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (32, 18), "black").save(frame)
    calls = []

    def system_one(state, questions, images):
        calls.append((state, list(questions), [image.size for image in images]))
        return _d1_response(
            {"drugs": 0.1, "nudity": 0.1, "sex": 0.1, "violence": 0.1},
            {"violence": 0, "sex": 0, "nudity": 0, "drugs": 0},
        )

    monkeypatch.setattr(d1_detector, "_load_model", lambda: SimpleNamespace(system_one=system_one))

    assert d1_detector.score_frame(frame).positives() == ()
    assert calls == [(None, list(d1_detector.questions()), [(32, 18)])]


def test_d1_loads_remote_code_only_from_the_verified_artifact(monkeypatch, tmp_path):
    loaded = []

    class _Model:
        def to(self, device):
            loaded.append(("to", device))
            return self

        def eval(self):
            return self

    # No torch.cuda: Windows/Linux runtimes exclude it, so the loader must not touch it.
    torch = SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        float32="float32",
        bfloat16="bfloat16",
    )
    transformers = SimpleNamespace(
        AutoModel=SimpleNamespace(
            from_pretrained=lambda path, **kwargs: loaded.append((path, kwargs)) or _Model()
        )
    )
    modules = {"torch": torch, "transformers": transformers}
    monkeypatch.setattr(d1_detector, "_CACHE", {})
    monkeypatch.setattr(d1_detector, "require_available", lambda _backend: None)
    monkeypatch.setattr(d1_detector, "_require_dependency", lambda name, _hint: modules[name])
    monkeypatch.setattr(d1_detector, "acquire_artifact", lambda artifact: tmp_path / artifact)
    monkeypatch.setattr(
        d1_detector, "current_environment", lambda: SimpleNamespace(torch_device="metal")
    )
    monkeypatch.setattr(d1_detector, "d1_transformers_supported", lambda: True)

    model = d1_detector._load_model()

    assert d1_detector._load_model() is model
    assert loaded == [
        (
            str(tmp_path / "d1-3b"),
            {"trust_remote_code": True, "local_files_only": True, "dtype": "bfloat16"},
        ),
        ("to", "mps"),
    ]
    monkeypatch.setattr(d1_detector, "_CACHE", {})
    monkeypatch.setattr(d1_detector, "d1_transformers_supported", lambda: False)
    with pytest.raises(RuntimeError, match="transformers>=5.14"):
        d1_detector._load_model()


def _fake_detector(monkeypatch, positives_by_index, scored):
    def score(frame_path):
        index = int(frame_path.stem)
        scored.append(index)
        families = positives_by_index.get(index, ())
        return FrameDecision(
            scores={family: 0.9 if family in families else 0.1 for family in QUESTIONS},
            thresholds=d1_detector.THRESHOLDS,
            levels={family: 1 for family in QUESTIONS},
        )

    detector = DecisionDetector("d1-3b", "d1", "LiquidAI/d1-3B", "rev", score)
    monkeypatch.setattr(decision_detection, "detector_for", lambda _video_model: detector)


def test_fine_pass_scores_a_strided_subset_and_reports_findings(monkeypatch):
    scored = []
    positives = {index: (F.VIOLENCE,) for index in range(5, 9)}
    _fake_detector(monkeypatch, positives, scored)
    monkeypatch.setattr(settings, "decision_fine_sample_interval_seconds", 0.5)
    frames = [(Path(f"{index}.jpg"), index * 0.125) for index in range(13)]
    progress = []

    findings = visual.scan_frames(
        frames,
        "scan-1",
        video_model="d1-3b",
        frame_interval_seconds=0.125,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert len(set(scored)) < len(frames)
    assert [finding.start_seconds for finding in findings] == [i * 0.125 for i in range(5, 9)]
    assert {(f.category, f.severity, f.severity_source) for f in findings} == {
        (F.VIOLENCE, Severity.LOW, "detector")
    }
    assert progress[-1] == (13, 13)


def test_coarse_sample_dispatches_to_the_selected_decision_model(monkeypatch):
    _fake_detector(monkeypatch, {0: (F.DRUGS,)}, [])

    [detection] = visual.scan_frame(Path("0.jpg"), 2.0, video_model="qwen3-vl-8b")

    assert (detection.category, detection.timestamp, detection.label) == (F.DRUGS, 2.0, "d1_drugs")


_NAN = float("nan")


@pytest.mark.parametrize(
    "probabilities",
    [
        None,  # no score answer at all
        {},
        {"0": _NAN, "1": _NAN, "2": _NAN, "3": _NAN},
        {"0": 0.1, "1": 0.2, "2": 0.7},  # wrong number of levels
        {"0": 0.1, "1": 0.2, "2": 0.3, "3": 0.4, "4": 0.0},
        {"0": 0.0, "1": 0.0, "2": 0.0, "3": 0.0},
        {"0": -0.5, "1": 0.2, "2": 0.2, "3": 0.1},
        {"0": 0.1, "1": float("inf"), "2": 0.1, "3": 0.1},
        {"0": "0.9", "1": 0.0, "2": 0.0, "3": 0.0},
    ],
)
def test_unusable_d1_severity_defers_to_the_description_model(probabilities):
    from scanner.pipeline.severity import resolve_severity

    response = _d1_response(
        {"drugs": 0.1, "nudity": 0.1, "sex": 0.1, "violence": 0.9},
        {"sex": 0, "nudity": 0, "drugs": 0},
    )
    if probabilities is not None:
        response["answers"]["sev_violence"] = {"type": "score", "probabilities": probabilities}

    decision = d1_detector.parse_answers(response)
    [detection] = decision_detection.detections_for_decision(decision, 0.0, d1_detector.DETECTOR)

    assert F.VIOLENCE not in decision.levels
    assert decision.levels[F.SEX] == 0
    assert detection.severity_source == "confidence"
    finding = visual._findings_from_detections(
        [detection], video_id="v", frame_interval_seconds=1.0, video_duration_seconds=None
    )
    # A graphic (level 2) description now wins instead of an invented detector level.
    assert resolve_severity(finding, 2) == (Severity.MEDIUM, "description_model")


def test_valid_d1_severity_vectors_are_unchanged():
    assert d1_detector.severity_level(
        {"probabilities": {"0": 0.1, "1": 0.2, "2": 0.6, "3": 0.1}}, 4
    ) == 2
    assert d1_detector.severity_level({"probabilities": {"0": 1, "1": 0, "2": 0}}, 3) == 0


def test_d1_uses_float32_cpu_when_metal_is_not_verified(monkeypatch, tmp_path):
    loaded = []
    torch = SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        float32="float32",
        bfloat16="bfloat16",
    )
    model = SimpleNamespace(to=lambda device: loaded.append(device) or model, eval=lambda: model)
    transformers = SimpleNamespace(
        AutoModel=SimpleNamespace(
            from_pretrained=lambda _path, **kwargs: loaded.append(kwargs["dtype"]) or model
        )
    )
    modules = {"torch": torch, "transformers": transformers}
    monkeypatch.setattr(d1_detector, "_CACHE", {})
    monkeypatch.setattr(d1_detector, "require_available", lambda _backend: None)
    monkeypatch.setattr(d1_detector, "_require_dependency", lambda name, _hint: modules[name])
    monkeypatch.setattr(d1_detector, "acquire_artifact", lambda artifact: tmp_path / artifact)
    monkeypatch.setattr(d1_detector, "d1_transformers_supported", lambda: True)
    monkeypatch.setattr(
        d1_detector, "current_environment", lambda: SimpleNamespace(torch_device="cpu")
    )

    d1_detector._load_model()

    assert loaded == ["float32", "cpu"]


@pytest.mark.parametrize(
    "response",
    [
        _d1_response({"drugs": _NAN, "nudity": 0.9, "sex": 0.1, "violence": 0.1}, {"nudity": 2}),
        _d1_response({"drugs": "0.9", "nudity": 0.9, "sex": 0.1, "violence": 0.1}, {"nudity": 2}),
        _d1_response({"nudity": 0.9, "sex": 0.1, "violence": 0.1}, {"nudity": 2}),
    ],
)
def test_unusable_d1_decision_leaves_only_that_family_undecided(monkeypatch, caplog, response):
    monkeypatch.setattr(d1_detector, "_WARNED", set())

    for _ in range(3):
        decision = d1_detector.parse_answers(response)

    assert F.DRUGS not in decision.scores
    assert decision.positives() == (F.NUDITY,)
    assert decision.levels == {F.NUDITY: 2}
    warnings = [record for record in caplog.records if "drugs decision" in record.getMessage()]
    assert len(warnings) == 1


@pytest.mark.parametrize("response", [{}, {"answers": None}, None])
def test_frame_without_answers_is_skipped_without_failing_the_scan(monkeypatch, response):
    monkeypatch.setattr(d1_detector, "_WARNED", set())

    decision = d1_detector.parse_answers(response)

    assert decision.positives() == ()
    assert decision.levels == {}
