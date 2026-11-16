import json
from pathlib import Path

import pytest

from scanner.commands import credits_eval_cli
from scanner.credits.credits_eval import (
    detect_pyscenedetect_boundaries,
    evaluate_credits_corpus,
    load_credits_evaluation_corpus,
)
from scanner.pipeline.processes import ScanCancelledError

CORPUS_PATH = Path(__file__).parent / "fixtures" / "mts" / "credits-evaluation-v1.json"


def test_credits_evaluator_reports_required_metrics():
    report = evaluate_credits_corpus(CORPUS_PATH)

    assert report["corpus"]["version"] == "mts-credits-evaluation-v1"
    assert report["corpus"]["license"] == "CC0-1.0"
    assert report["candidate_recall"] == {
        "within_tolerance": 7,
        "positive_cases": 7,
        "rate": 1.0,
    }
    assert report["false_positives"] == {
        "count": 0,
        "null_boundary_cases": 1,
        "rate": 0.0,
    }
    assert report["absolute_boundary_error_seconds"] == {"mean": 0.0, "maximum": 0.0}
    assert report["confidence_calibration"]["low"]["candidate_count"] >= 1
    assert report["rendered_tail_validation"]["candidate_recall"]["rate"] == 1.0
    assert report["rendered_tail_validation"]["false_positives"]["count"] == 0
    assert report["rendered_tail_validation"]["peak_python_memory_bytes"] > 0
    comparison = report["third_party_comparison"]["pyscenedetect"]
    assert comparison["package_version"] == "0.7.1"
    assert comparison["candidate_recall"] == {
        "within_tolerance": 6,
        "positive_cases": 7,
        "rate": 6 / 7,
    }
    assert comparison["false_positives"]["count"] == 0
    assert comparison["model_artifact_size_bytes"] == 0
    assert comparison["package_artifact_size_bytes"] == 146_194
    assert comparison["absolute_boundary_error_seconds"] == {
        "mean": 0.0,
        "maximum": 0.0,
    }
    assert comparison["confidence_band_counts"] == {"low": 0, "medium": 4, "high": 2}
    assert comparison["peak_python_memory_bytes"] > 0
    missed_cases = [case["id"] for case in comparison["cases"] if not case["within_tolerance"]]
    assert missed_cases == ["intermittent-black-frames"]
    assert report["limitations"]


def test_pyscenedetect_comparison_adapter_honors_cancellation():
    with pytest.raises(ScanCancelledError, match="Scan cancelled"):
        detect_pyscenedetect_boundaries(
            [(Path("not-read.png"), 1.0)], should_cancel=lambda: True
        )


def test_pyscenedetect_comparison_adapter_rejects_unreadable_frame(tmp_path: Path):
    with pytest.raises(ValueError, match="Could not read sampled frame"):
        detect_pyscenedetect_boundaries([(tmp_path / "missing.png", 1.0)])


def test_credits_corpus_rejects_missing_required_coverage(tmp_path: Path):
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    payload["cases"] = payload["cases"][:1]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required tags"):
        load_credits_evaluation_corpus(path)


def test_credits_evaluator_cli_writes_json(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "report.json"
    monkeypatch.setattr(
        "sys.argv",
        ["editarr-evaluate-credits", str(CORPUS_PATH), "--output", str(output_path)],
    )

    credits_eval_cli.main()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["candidate_recall"]["rate"] == 1.0
