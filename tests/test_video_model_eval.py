import json
from pathlib import Path

from scanner.evaluation import video_model_eval
from scanner.models import McfCategoryFamily, Severity
from scanner.pipeline.visual import VisualDetection


def test_video_model_evaluation_reports_speed_and_category_metrics(monkeypatch, tmp_path: Path):
    (tmp_path / "clean.jpg").write_bytes(b"clean")
    (tmp_path / "fight.jpg").write_bytes(b"fight")
    dataset = tmp_path / "manifest.json"
    dataset.write_text(
        json.dumps(
            {
                "frames": [
                    {"path": "clean.jpg", "expected_categories": []},
                    {"path": "fight.jpg", "expected_categories": ["violence"]},
                ]
            }
        )
    )
    calls = []

    def scan(frame_path: Path, _timestamp: float, *, video_model: str):
        calls.append((frame_path.name, video_model))
        if frame_path.name == "fight.jpg":
            return [
                VisualDetection(
                    timestamp=0,
                    label="violent_scene",
                    confidence=0.9,
                    category=McfCategoryFamily.VIOLENCE,
                    subcategory="violence",
                    severity=Severity.HIGH,
                )
            ]
        return [
            VisualDetection(
                timestamp=0,
                label="false_positive",
                confidence=0.6,
                category=McfCategoryFamily.NUDITY,
                subcategory="nudity",
                severity=Severity.LOW,
            )
        ]

    monkeypatch.setattr(video_model_eval, "scan_frame", scan)

    result = video_model_eval.evaluate_video_model(
        dataset, "siglip2-moderation-violence", warmup_frames=1, runs=2
    )

    assert calls == [
        ("clean.jpg", "siglip2_moderation_violence"),
        ("clean.jpg", "siglip2_moderation_violence"),
        ("fight.jpg", "siglip2_moderation_violence"),
        ("clean.jpg", "siglip2_moderation_violence"),
        ("fight.jpg", "siglip2_moderation_violence"),
    ]
    assert result["timing"]["measured_calls"] == 4
    assert result["accuracy"]["overall"] == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 0,
        "precision": 0.5,
        "recall": 1.0,
        "f1": 2 / 3,
    }
    assert result["accuracy"]["by_category"]["violence"]["true_positive"] == 1
    assert result["accuracy"]["by_category"]["nudity"]["false_positive"] == 1
