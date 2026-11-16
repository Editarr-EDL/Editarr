from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.evaluation.subcategorization import (
    SubcategorizationMethod,
    SubcategorizationResult,
)
from scanner.models import FindingCreate, McfCategory, McfCategoryFamily, Severity
from scanner.pipeline import visual
from scanner.pipeline.processes import ScanCancelledError
from scanner.pipeline.qwen_summary import QwenSummary


@pytest.fixture(autouse=True)
def _available_qwen(monkeypatch):
    """Exercise Qwen segment mechanics independently of the current platform."""

    monkeypatch.setattr(
        visual,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(availability="available"),
    )


def _finding(timestamp: float) -> FindingCreate:
    return FindingCreate(
        video_id="scan-1",
        category=McfCategoryFamily.VIOLENCE,
        subcategory="violence",
        start_seconds=timestamp,
        end_seconds=timestamp + 1,
        severity=Severity.HIGH,
        confidence=0.9,
        label="siglip2_moderation_violence",
        details="Detected violence",
        group_key="violence",
        source="visual",
    )


def test_qwen_events_require_real_summary_work(monkeypatch, tmp_path: Path):
    events = []
    frames = [(tmp_path / "frame-0.jpg", 0.0)]
    assert visual.summarize_flagged_segments(
        [], frames, summaries_per_segment=1, join_gap_seconds=0,
        provenance_callback=events.append,
    ) == []
    assert events == ["no_inputs"]

    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda *_args, **_kwargs: QwenSummary(("violence",), "Visible violence."),
    )
    events.clear()
    visual.summarize_flagged_segments(
        [_finding(0)], frames, summaries_per_segment=1, join_gap_seconds=0,
        provenance_callback=events.append,
    )
    assert events == ["started", "complete"]

    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("inference failed")),
    )
    events.clear()
    with pytest.raises(RuntimeError, match="inference failed"):
        visual.summarize_flagged_segments(
            [_finding(0)], frames, summaries_per_segment=1, join_gap_seconds=0,
            provenance_callback=events.append,
        )
    assert events == ["started", "failed"]


def test_qwen_summaries_are_deferred_until_after_the_safety_scan(monkeypatch, tmp_path: Path):
    frames = [(tmp_path / f"frame-{index}.jpg", float(index)) for index in range(3)]
    monkeypatch.setattr(
        visual,
        "_scan_frames_nudenet",
        lambda source_frames: iter(
            [
                [
                    visual.VisualDetection(
                        timestamp=timestamp,
                        label="flagged",
                        confidence=0.9,
                        category=McfCategoryFamily.VIOLENCE,
                        subcategory="violence",
                        severity=Severity.HIGH,
                    )
                ]
                for _path, timestamp in source_frames
            ]
        ),
    )
    summaries = []
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda paths, *, families, qwen_model: summaries.append(
            (paths, families, qwen_model)
        )
        or QwenSummary(("violence",), "Violence is visible."),
    )

    findings = visual.scan_frames(frames, "scan-1", frame_interval_seconds=1)

    assert summaries == []
    summarized = visual.summarize_flagged_segments(
        findings,
        frames,
        qwen_model="qwen3-vl-4b-instruct-4bit",
        summaries_per_segment=2,
        join_gap_seconds=0,
    )
    assert summaries == [
        (
            [frames[0][0], frames[2][0]],
            (McfCategoryFamily.VIOLENCE,),
            "qwen3-vl-4b-instruct-4bit",
        )
    ]
    assert len(summarized) == 1
    assert summarized[0].start_seconds == 0
    assert summarized[0].end_seconds == 3


def test_qwen_joins_short_clean_gaps_and_chooses_nearest_flagged_frames(
    monkeypatch, tmp_path: Path
):
    frames = [(tmp_path / f"frame-{index}.jpg", float(index)) for index in range(7)]
    findings = [_finding(timestamp) for timestamp in (0, 1, 2, 5, 6)]
    summaries: list[list[float]] = []

    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda paths, *, families, qwen_model: summaries.append(
            [float(path.stem.removeprefix("frame-")) for path in paths]
        )
        or QwenSummary(("violence",), "A direct image description."),
    )

    summarized = visual.summarize_flagged_segments(
        findings,
        frames,
        summaries_per_segment=4,
        join_gap_seconds=2,
    )

    assert summaries == [[0, 2, 5, 6]]
    assert len(summarized) == 1
    assert summarized[0].start_seconds == 0
    assert summarized[0].end_seconds == 7
    assert summarized[0].details == "A direct image description."


def test_qwen_keeps_segments_separate_when_the_clean_gap_is_too_large(monkeypatch, tmp_path: Path):
    frames = [(tmp_path / f"frame-{index}.jpg", float(index)) for index in range(7)]
    findings = [_finding(timestamp) for timestamp in (0, 1, 2, 5, 6)]
    summaries: list[list[float]] = []
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda paths, *, families, qwen_model: summaries.append(
            [float(path.stem.removeprefix("frame-")) for path in paths]
        )
        or QwenSummary(("violence",), "Violence is visible."),
    )

    summarized = visual.summarize_flagged_segments(
        findings,
        frames,
        summaries_per_segment=4,
        join_gap_seconds=1.9,
    )

    assert summaries == [[0, 1, 2], [5, 6]]
    assert [(finding.start_seconds, finding.end_seconds) for finding in summarized] == [
        (0, 3),
        (5, 7),
    ]


@pytest.mark.parametrize(
    "category",
    ["No objectionable content", "No objectionable content detected"],
)
def test_qwen_clean_summary_is_persisted_unselected(
    monkeypatch, tmp_path: Path, category: str
):
    frames = [(tmp_path / "frame-0.jpg", 0.0)]
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda _paths, *, families, qwen_model: QwenSummary(
            (category,),
            f"{category}.",
        ),
    )

    summarized = visual.summarize_flagged_segments(
        [_finding(0.0)],
        frames,
        summaries_per_segment=1,
        join_gap_seconds=0,
    )

    assert len(summarized) == 1
    assert summarized[0].selected is False
    assert summarized[0].group_key == "No objectionable content"


def test_qwen_summary_preserves_each_detector_classification(monkeypatch, tmp_path: Path):
    frames = [(tmp_path / "frame-0.jpg", 0.0)]
    nudity = _finding(0.0).model_copy(
        update={
            "category": McfCategoryFamily.NUDITY,
            "subcategory": "bareButtocks",
            "label": "BUTTOCKS_EXPOSED",
        }
    )
    violence = _finding(0.0)
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda _paths, *, families, qwen_model: QwenSummary(
            ("nudity", "violence"), "Both are visible."
        ),
    )

    summarized = visual.summarize_flagged_segments(
        [nudity, violence],
        frames,
        summaries_per_segment=1,
        join_gap_seconds=0,
    )

    assert [
        (finding.category.value, finding.subcategory.value) for finding in summarized
    ] == [("nudity", "bareButtocks"), ("violence", "violence")]


def test_qwen_can_only_specialize_a_general_detector_fallback(monkeypatch, tmp_path: Path):
    frames = [(tmp_path / "frame-0.jpg", 0.0)]
    general = _finding(0.0)
    exact = _finding(0.0).model_copy(
        update={
            "category": McfCategoryFamily.NUDITY,
            "subcategory": "bareButtocks",
            "label": "BUTTOCKS_EXPOSED",
        }
    )
    qwen_results = (
        SubcategorizationResult(
            category=McfCategoryFamily.VIOLENCE,
            subcategory=McfCategory("weapons"),
            method=SubcategorizationMethod.QWEN_SUMMARY,
            producer="qwen_segment_summary",
            confidence=0.97,
            decision_evidence=("A firearm is visible.",),
            model_id="mlx-community/Qwen3-VL-8B-Instruct-4bit",
            model_revision="test-revision",
        ),
        SubcategorizationResult(
            category=McfCategoryFamily.NUDITY,
            subcategory=McfCategory("toplessness"),
            method=SubcategorizationMethod.QWEN_SUMMARY,
            producer="qwen_segment_summary",
            confidence=0.96,
            decision_evidence=("An uncovered upper body is visible.",),
            model_id="mlx-community/Qwen3-VL-8B-Instruct-4bit",
            model_revision="test-revision",
        ),
    )
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda _paths, *, families, qwen_model: QwenSummary(
            ("violence", "nudity"),
            "Visible content.",
            qwen_results,
            True,
        ),
    )

    summarized = visual.summarize_flagged_segments(
        [general, exact],
        frames,
        summaries_per_segment=1,
        join_gap_seconds=0,
    )

    assert [finding.subcategory.value for finding in summarized] == [
        "weapons",
        "bareButtocks",
    ]


def test_qwen_cancellation_stops_before_model_inference(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda *_args, **_kwargs: pytest.fail("cancelled Qwen must not run"),
    )

    with pytest.raises(ScanCancelledError, match="Scan cancelled"):
        visual.summarize_flagged_segments(
            [_finding(0.0)],
            [(tmp_path / "frame-0.jpg", 0.0)],
            summaries_per_segment=1,
            join_gap_seconds=0,
            should_cancel=lambda: True,
        )
