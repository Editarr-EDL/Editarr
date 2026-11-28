"""Inputs to describe-eval: failed description turns and severity ground truth."""

from __future__ import annotations

import pytest

from scanner.evaluation.folder_eval.describe_eval import MENTION_KEYS, evaluate_model
from scanner.evaluation.folder_eval.paths import Layout
from scanner.evaluation.folder_eval.scoring import score_item
from scanner.evaluation.folder_eval.severity import SeverityMap, window_severity
from tests.folder_eval_support import clip


class _Describer:
    model_id = "describer"

    def __init__(self, outputs):
        self.outputs = outputs

    def score_frames(self, paths):
        return self.outputs[: len(paths)]


def test_failed_description_turns_reach_describe_eval(tmp_path):
    item = clip("a", "video/x", ["violence"], frames=2)
    outputs = [
        {"scores": {"violence": 0.9}, "description": "A fight.", "describe": "ok"},
        {"scores": {"violence": 0.1}, "description": None, "describe": "failed"},
    ]
    record = score_item(
        _Describer(outputs), Layout(tmp_path), item, item["frames"], {"*": 0.5}, None
    )
    failed = record["frames"][1]
    assert failed["description"] is None and failed["describe"] == "failed"

    class Judge:
        def mentions(self, text):
            return {key: float(key in text) for key in MENTION_KEYS}

    rows = [
        {"expected": ["violence"], "truth_severity": None, "frame": frame}
        for frame in record["frames"]
    ]
    assert evaluate_model(rows, Judge())["json_failure_rate"] == 0.5


def test_backends_without_descriptions_get_no_description_field(tmp_path):
    item = clip("a", "video/x", [], frames=1)
    record = score_item(
        _Describer([{"scores": {}, "transcript": None}]), Layout(tmp_path), item,
        item["frames"], {}, None,
    )
    assert record["frames"] == [{"n": 0, "scores": {}}]


def test_only_a_family_level_absent_correction_zeroes_severity():
    window = {"id": "w", "window": [0, 10], "source_tags": ["violence/graphic"]}
    severity = SeverityMap.load()
    base = window_severity(window, None, severity)["violence"]
    assert base > 0
    token_absent = window_severity(window, {"violence/weapons": False}, severity)
    assert token_absent["violence"] == base
    assert window_severity(window, {"violence": False}, severity)["violence"] == 0


def test_cascade_keeps_structured_describer_replies():
    from scanner.evaluation.folder_eval.decision.cascade import described_fields

    assert described_fields({"transcript": ["A fight."]}) == {
        "description": "A fight.", "describe": "ok",
    }
    assert described_fields(
        {"scores": {}, "description": "Two men fight.", "describe": "retried"}
    ) == {"description": "Two men fight.", "describe": "retried"}
    assert described_fields({"scores": {}, "description": None, "describe": "failed"}) == {
        "description": None, "describe": "failed",
    }
    assert described_fields({"scores": {}}) == {"description": "none", "describe": "ok"}


@pytest.mark.parametrize(
    "answer",
    [
        None,
        {"probabilities": [0.5, 0.5]},
        {"probabilities": [0.0, 0.0, 0.0, 0.0]},
        {"probabilities": [0.1, float("nan"), 0.2, 0.1]},
        {"probabilities": [0.1, float("inf"), 0.2, 0.1]},
        {"probabilities": [-0.1, 0.5, 0.3, 0.3]},
        {"probabilities": {"0": 0.2, "1": 0.8}},
        {"probabilities": [0.1, True, 0.2, 0.1]},
    ],
)
def test_unusable_d1_severity_answers_give_no_level(answer):
    from scanner.evaluation.folder_eval.decision.d1_severity import parse_severity

    answers = {"sev_violence": answer, "sev_drugs": {"probabilities": [0.2, 0.3, 0.5]}}
    levels, expected = parse_severity({"answers": answers})
    assert "violence" not in levels and "violence" not in expected
    assert levels["drugs"] == 2


def test_onejson_out_of_range_severity_is_left_out():
    import json

    from scanner.evaluation.folder_eval.decision.qwen_onejson import BOOLEAN_KEYS, parse_onejson

    reply = {field: False for field in BOOLEAN_KEYS}
    severity = {"violence": 9, "sex": 0, "nudity": 0, "drugs": 3}
    reply.update(description="A scene.", severity=severity)
    assert parse_onejson(json.dumps(reply)) == ("A scene.", {"sex": 0, "nudity": 0})


def test_severity_metrics_exclude_and_count_unusable_predictions(tmp_path):
    from scanner.evaluation.folder_eval.severity import severity_metrics

    metrics = severity_metrics([2, 2, 1, None, 0], [2, None, 7, None, True], 3)
    assert metrics == {
        "n": 1, "unusable": 3, "exact": 1.0, "within_1": 1.0, "kappa": metrics["kappa"],
    }

    item = clip("a", "films/F", ["violence"], frames=2)
    outputs = [
        {"scores": {}, "description": "A fight.", "describe": "ok",
         "severity": {"violence": 2, "sex": 0, "nudity": 0}},
        {"scores": {}, "description": None, "describe": "failed", "severity": None},
    ]
    record = score_item(
        _Describer(outputs), Layout(tmp_path), item, item["frames"], {"*": 0.5}, None
    )
    assert record["frames"][1]["severity"] is None
    truth = {"violence": 2, "sex": 0, "nudity": 0, "drugs": 0}
    rows = [{"expected": [], "truth_severity": truth, "frame": f} for f in record["frames"]]

    class Judge:
        def mentions(self, text):
            return {key: 0.0 for key in MENTION_KEYS}

    severity = evaluate_model(rows, Judge())["severity"]
    assert severity["violence"]["n"] == 1 and severity["violence"]["unusable"] == 1
    assert severity["drugs"] == {
        "n": 0, "unusable": 2, "exact": None, "within_1": None, "kappa": None,
    }


def test_kappa_and_metrics_handle_degenerate_inputs_and_per_key_scales():
    from scanner.evaluation.folder_eval.severity import quadratic_kappa, severity_metrics

    assert quadratic_kappa([], 4) is None
    assert quadratic_kappa([(0, 0)], 1) is None
    assert quadratic_kappa([(0, 0), (0, 0)], 4) is None  # one class: no expected disagreement
    assert quadratic_kappa([(0, 0), (0, 2)], 3) == 0.0
    assert quadratic_kappa([(0, 0), (2, 2)], 3) == 1.0
    # drugs is 0-2: a 3 is unusable, and a truth level beyond the scale is never indexed.
    drugs = severity_metrics([2, 2, 3, None], [3, 1, 1, 0], 2)
    assert (drugs["n"], drugs["unusable"], drugs["within_1"]) == (1, 1, 1.0)


def test_severity_map_rejects_levels_beyond_their_scale():
    with pytest.raises(ValueError, match="drugs"):
        SeverityMap({"levels": {"drugs": {"drugs/x": 3}}, "max_level": {"drugs": 2}})


@pytest.mark.parametrize(
    ("tags", "expected", "corrections", "truth"),
    [
        (["violence/graphic"], ["violence"], None, 2),
        (["violence/disturbing_images"], ["violence"], None, None),  # present, no level
        (["violence/violence_implied"], [], None, None),  # ambiguous
        ([], [], None, 0),
        (["violence/disturbing_images"], ["violence"], {"violence": False}, 0),
        ([], [], {"violence/weapons": True}, None),
        (["violence/gore"], ["violence"], {"violence/weapons": False}, 3),
    ],
)
def test_window_severity_truth(tags, expected, corrections, truth):
    window = {"id": "w", "window": [0, 10], "source_tags": tags, "expected": expected}
    assert window_severity(window, corrections, SeverityMap.load())["violence"] == truth
