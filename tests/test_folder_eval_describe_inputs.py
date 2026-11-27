"""Inputs to describe-eval: failed description turns and severity ground truth."""

from __future__ import annotations

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
        _Describer([{"scores": {}, "severity": None}]), Layout(tmp_path), item,
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
