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
