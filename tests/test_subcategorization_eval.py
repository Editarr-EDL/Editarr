import json
from pathlib import Path

import pytest

from scanner.commands import subcategorization_eval_cli
from scanner.evaluation.subcategorization_eval import (
    evaluate_detector_mapping_corpus,
    evaluate_subcategorization_corpus,
    load_subcategorization_corpus,
)

CORPUS_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "mcf"
    / "subcategorization-parser-corpus-v1.json"
)
DETECTOR_CORPUS_PATH = (
    Path(__file__).parent / "fixtures" / "mcf" / "detector-label-policy-corpus-v1.json"
)


def test_subcategorization_evaluator_reports_required_metrics():
    result = evaluate_subcategorization_corpus(CORPUS_PATH)

    assert result["corpus"] == {
        "id": "editarr-mcf12-parser-conformance-v1",
        "license": "CC0-1.0",
        "purpose": "parser-conformance-only-not-model-quality",
        "case_count": 6,
    }
    assert result["accuracy"]["overall"] == {
        "true_positive": 7,
        "false_positive": 0,
        "false_negative": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert result["general_fallback_rate"] == 5 / 7
    assert result["invalid_output_rate"] == 2 / 6
    assert result["base_detection_family_regressions"] == 0
    assert result["model_latency_ms"] == {
        "mean": 11.5,
        "p50": 11.0,
        "p95": 14.0,
        "max": 14.0,
    }


def test_subcategorization_corpus_rejects_out_of_family_expectation(tmp_path: Path):
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["expected"]["nudity"] = "weapons"
    corpus_path = tmp_path / "invalid.json"
    corpus_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="out-of-family expectation"):
        load_subcategorization_corpus(corpus_path)


def test_subcategorization_evaluator_cli_writes_json(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "editarr-evaluate-subcategorization",
            str(CORPUS_PATH),
            "--output",
            str(output_path),
        ],
    )

    subcategorization_eval_cli.main()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["corpus"]["id"] == "editarr-mcf12-parser-conformance-v1"
    assert report["base_detection_family_regressions"] == 0


def test_detector_mapping_evaluator_covers_all_supported_policies():
    result = evaluate_detector_mapping_corpus(DETECTOR_CORPUS_PATH)

    assert result["corpus"]["case_count"] == 13
    assert result["accuracy"]["overall"] == {
        "true_positive": 13,
        "false_positive": 0,
        "false_negative": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert result["accuracy"]["by_subcategory"]["bareButtocks"]["recall"] == 1.0
    assert result["accuracy"]["by_subcategory"]["exposedGenitalia"]["recall"] == 1.0
    assert result["general_fallback_rate"] == 10 / 13
    assert result["base_detection_family_regressions"] == 0


def test_subcategorization_evaluator_cli_supports_detector_label_corpus(
    monkeypatch, tmp_path: Path
):
    output_path = tmp_path / "detector-report.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "editarr-evaluate-subcategorization",
            str(DETECTOR_CORPUS_PATH),
            "--detector-labels",
            "--output",
            str(output_path),
        ],
    )

    subcategorization_eval_cli.main()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["corpus"]["id"] == "editarr-mcf12-detector-label-policy-v1"
    assert report["accuracy"]["overall"]["true_positive"] == 13
