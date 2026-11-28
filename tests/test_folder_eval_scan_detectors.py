"""Tests for the `scan-*` ids, which evaluate the decision detectors Editarr ships."""

from __future__ import annotations

import pytest

from scanner.evaluation.folder_eval.backends import BackendUnavailable
from scanner.evaluation.folder_eval.decision import scan_detectors
from scanner.model_acquisition import AcquisitionError
from scanner.models import McfCategoryFamily
from scanner.pipeline import decision_detection, detector_memory


def test_scan_detector_ids_score_with_the_shipped_detectors(monkeypatch, tmp_path):
    scored, released = [], []
    decision = decision_detection.FrameDecision(
        scores={McfCategoryFamily.VIOLENCE: 0.8, McfCategoryFamily.SEX: 0.1},
        thresholds={},
        levels={McfCategoryFamily.VIOLENCE: 2},
    )
    detector = decision_detection.DecisionDetector(
        "d1-3b", "d1", "LiquidAI/d1-3B", "rev", lambda path: scored.append(path) or decision
    )
    monkeypatch.setattr(decision_detection, "detector_for", lambda _model: detector)
    monkeypatch.setattr(detector_memory, "release_detector", released.append)

    backend = scan_detectors.ScanDetectorBackend("d1-3b")
    frame = tmp_path / "0.jpg"
    assert backend.score_frames([frame]) == [
        {"scores": {"violence": 0.8, "sex": 0.1}, "labels": [], "severity": {"violence": 2}}
    ]
    assert scored == [frame]
    meta = backend.meta()
    assert meta["model_id"] == "scan-d1-3b" and meta["thresholds"]["violence"] == 0.25
    backend.close()
    assert released == ["d1-3b"]


def test_scan_detector_is_unavailable_until_its_artifact_is_installed(monkeypatch):
    class Service:
        def verified_local_artifact(self, artifact_id, verify_content=True):
            raise AcquisitionError("not_ready", f"{artifact_id} is missing")

    monkeypatch.setattr("scanner.model_acquisition.get_acquisition_service", Service)
    monkeypatch.setattr(
        "scanner.settings.capabilities.capability_for",
        lambda _model: type("Capability", (), {"availability": "available"})(),
    )
    with pytest.raises(BackendUnavailable, match="qwen3-vl-4b-instruct-4bit is not installed"):
        scan_detectors.ScanDetectorBackend("qwen3-vl-4b").check_available()


def _scan_frame(monkeypatch, tmp_path, video_model):
    from scanner.evaluation.folder_eval.scoring import score_item
    from tests.folder_eval_support import cached_layout

    decision = decision_detection.FrameDecision(
        scores={McfCategoryFamily.VIOLENCE: 0.8}, thresholds={}, levels={}
    )
    detector = decision_detection.DecisionDetector(
        video_model, "x", "model", "rev", lambda _path: decision
    )
    monkeypatch.setattr(decision_detection, "detector_for", lambda _model: detector)
    item = {"id": "a", "path": "films/F/a.mkv", "frames": [{"n": 0, "file": "frames/a/0.jpg"}]}
    backend = scan_detectors.ScanDetectorBackend(video_model)
    record = score_item(backend, cached_layout(tmp_path, item), item, item["frames"], {}, None)
    return record["frames"][0]


def _severity_report(frame):
    from scanner.evaluation.folder_eval.describe_eval import evaluate_model

    truth = {"violence": 2, "sex": 0, "nudity": 0, "drugs": 0}
    rows = [{"expected": ["violence"], "truth_severity": truth, "frame": frame}]
    return evaluate_model(rows, judge=None).get("severity")


def test_d1_frames_with_only_unusable_severity_are_counted(monkeypatch, tmp_path):
    frame = _scan_frame(monkeypatch, tmp_path, "d1-3b")
    assert frame["severity"] == {}
    report = _severity_report(frame)
    assert all(metrics == {"n": 0, "unusable": 1, "exact": None, "within_1": None,
                           "kappa": None} for metrics in report.values())


def test_qwen_detector_frames_have_no_severity_to_count(monkeypatch, tmp_path):
    frame = _scan_frame(monkeypatch, tmp_path, "qwen3-vl-8b")
    assert "severity" not in frame
    assert _severity_report(frame) is None


def test_d1_sev_and_cascade_keep_an_empty_severity_map(monkeypatch, tmp_path):
    from scanner.evaluation.folder_eval.decision.cascade import CascadeBackend
    from scanner.evaluation.folder_eval.decision.registry import BACKENDS

    backend = BACKENDS["d1-3b-sev"]()
    monkeypatch.setattr(backend, "_ensure_server", lambda: None)
    monkeypatch.setattr(backend, "_post", lambda request: {"answers": {
        qid: {"noul": 0.1} for qid, q in request["questions"].items() if q["type"] == "noul"
    }})
    (tmp_path / "f.jpg").write_bytes(b"x")
    (frame,) = backend.score_frames([tmp_path / "f.jpg"])
    assert frame["severity"] == {}

    cascade = CascadeBackend(decider=lambda: backend, describer=lambda: pytest.fail("described"))
    ((cascaded,),) = cascade.score_items([[tmp_path / "f.jpg"]])
    assert cascaded["severity"] == {} and cascaded["describe"] == "skipped"
