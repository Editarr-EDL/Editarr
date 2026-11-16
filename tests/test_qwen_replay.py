import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.models import FindingCreate, McfCategoryFamily, Severity
from scanner.pipeline import qwen_summary, visual


@pytest.fixture(autouse=True)
def _available_qwen(monkeypatch):
    monkeypatch.setattr(
        visual,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available"),
    )


def _finding(timestamp: float) -> FindingCreate:
    return FindingCreate(
        video_id="video-1",
        category=McfCategoryFamily.VIOLENCE,
        subcategory="violence",
        start_seconds=timestamp,
        end_seconds=timestamp + 1,
        severity=Severity.HIGH,
        confidence=0.9,
        label="image_safety_gate_nsfl",
        details="Detector finding",
        group_key="violence",
        source="visual",
    )


def test_qwen_replay_reuses_saved_frames_and_accepts_prompt_overrides(monkeypatch, tmp_path: Path):
    frame_paths = []
    for timestamp in (0.0, 1.0):
        frame_path = tmp_path / f"source-{timestamp}.jpg"
        frame_path.write_bytes(b"frame")
        frame_paths.append((frame_path, timestamp))
    replay_dir = tmp_path / "replay"

    monkeypatch.setattr(
        qwen_summary,
        "summarize_frames",
        lambda _frame_paths,
        *,
        prompt=qwen_summary.PROMPT,
        families,
        qwen_model: qwen_summary.QwenSummary(("violence",), f"Initial {prompt}"),
    )

    visual.summarize_flagged_segments(
        [_finding(0.0), _finding(1.0)],
        frame_paths,
        qwen_model="qwen3-vl-4b-instruct-4bit",
        summaries_per_segment=2,
        join_gap_seconds=3.0,
        replay_dir=replay_dir,
    )

    manifest = json.loads((replay_dir / "manifest.json").read_text())
    assert manifest["qwen_model"] == "qwen3-vl-4b-instruct-4bit"
    assert manifest["summaries_per_segment"] == 2
    assert manifest["segments"][0]["frames"] == [
        {"filename": "segment_0001_frame_01.jpg", "timestamp": 0.0},
        {"filename": "segment_0001_frame_02.jpg", "timestamp": 1.0},
    ]
    assert (replay_dir / "segment_0001_frame_01.jpg").read_bytes() == b"frame"

    prompts = []
    monkeypatch.setattr(
        qwen_summary,
        "summarize_frames",
        lambda paths, *, prompt, families, qwen_model: (
            prompts.append(([path.name for path in paths], prompt, families, qwen_model))
            or qwen_summary.QwenSummary(("weapons",), "A gun is visible.")
        ),
    )

    replayed = visual.replay_qwen_summaries(
        replay_dir,
        prompt="Describe only weapons.",
    )

    assert prompts == [
        (
            ["segment_0001_frame_01.jpg", "segment_0001_frame_02.jpg"],
            "Describe only weapons.",
            (McfCategoryFamily.VIOLENCE,),
            "qwen3-vl-4b-instruct-4bit",
        ),
    ]
    assert replayed[0].details == "A gun is visible."
    assert replayed[0].group_key == "weapons"


def test_qwen_replay_loads_pre_mcf_findings_with_general_fallbacks(
    monkeypatch, tmp_path: Path
):
    replay_dir = tmp_path / "legacy-replay"
    replay_dir.mkdir()
    frame_path = replay_dir / "legacy-frame.jpg"
    frame_path.write_bytes(b"frame")
    manifest = {
        "version": 1,
        "segments": [
            {
                "end_seconds": 2.0,
                "findings": [
                    {
                        "video_id": "legacy-scan",
                        "category": "nudity",
                        "start_seconds": 1.0,
                        "end_seconds": 2.0,
                        "severity": "medium",
                        "confidence": 0.8,
                        "label": "legacy-nsfw",
                        "source": "visual",
                    }
                ],
                "frames": [{"filename": frame_path.name, "timestamp": 1.0}],
            }
        ],
    }
    (replay_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        qwen_summary,
        "summarize_frames",
        lambda _paths, *, prompt, families, qwen_model: (
            qwen_summary.QwenSummary(("nudity",), f"Legacy replay summary ({qwen_model}).")
        ),
    )

    replayed = visual.replay_qwen_summaries(replay_dir)

    assert replayed[0].category == McfCategoryFamily.NUDITY
    assert replayed[0].subcategory.value == "nudity"
    assert replayed[0].details == "Legacy replay summary (qwen3-vl-8b-instruct-4bit)."
    unchanged = json.loads((replay_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "subcategory" not in unchanged["segments"][0]["findings"][0]
