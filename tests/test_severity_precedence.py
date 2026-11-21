"""Severity rubric parsing and the detector > description > confidence precedence."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.evaluation.severity_rubric import extract_severities, level_severity, parse_level
from scanner.models import FindingCreate, McfCategoryFamily, Severity
from scanner.pipeline import visual
from scanner.pipeline.qwen_summary import QwenSummary
from scanner.pipeline.severity import resolve_severity

VIOLENCE = McfCategoryFamily.VIOLENCE


@pytest.mark.parametrize(
    ("value", "family", "expected"),
    [
        (2, VIOLENCE, 2),
        (2.0, VIOLENCE, 2),
        (" 3 ", VIOLENCE, 3),
        (7, VIOLENCE, 3),
        (3, McfCategoryFamily.DRUGS, 2),
        (0, VIOLENCE, 0),
        (-1, VIOLENCE, None),
        (1.5, VIOLENCE, None),
        (True, VIOLENCE, None),
        ("high", VIOLENCE, None),
        (None, VIOLENCE, None),
        (2, McfCategoryFamily.FEAR, None),
    ],
)
def test_parse_level_is_robust(value, family, expected):
    assert parse_level(value, family) == expected


def test_levels_map_to_mcf_severity_and_level_zero_is_low():
    assert [level_severity(level) for level in (0, 1, 2, 3)] == [
        Severity.LOW,
        Severity.LOW,
        Severity.MEDIUM,
        Severity.HIGH,
    ]


def test_extract_severities_ignores_unrequested_families_and_clean_responses():
    response = (
        '```json\n{"objectionable": true, "classifications": ['
        '{"family": "violence", "severity": 3}, {"family": "sex", "severity": 2}, '
        '{"family": "unknown", "severity": 1}, "junk"]}\n```'
    )

    assert extract_severities(response, (VIOLENCE,)) == {VIOLENCE: 3}
    assert extract_severities('{"objectionable": false, "classifications": []}', (VIOLENCE,)) == {}
    assert extract_severities("not json", (VIOLENCE,)) == {}


def _finding(timestamp, severity, source, confidence=0.9):
    return FindingCreate(
        video_id="scan-1",
        category=VIOLENCE,
        subcategory="violence",
        start_seconds=timestamp,
        end_seconds=timestamp + 1,
        severity=severity,
        severity_source=source,
        confidence=confidence,
        label="detector_violence",
        source="visual",
    )


@pytest.mark.parametrize(
    ("findings", "level", "expected"),
    [
        # Detector severity wins over a description-model level.
        ([_finding(0, Severity.LOW, "detector")], 3, (Severity.LOW, "detector")),
        # Description-model level of at least 1 replaces the confidence mapping.
        ([_finding(0, Severity.HIGH, "confidence")], 1, (Severity.LOW, "description_model")),
        # Level 0 or no level keeps the confidence fallback.
        ([_finding(0, Severity.HIGH, "confidence")], 0, (Severity.HIGH, "confidence")),
        ([_finding(0, Severity.MEDIUM, None)], None, (Severity.MEDIUM, "confidence")),
        (
            [_finding(0, Severity.LOW, "detector"), _finding(1, Severity.HIGH, "detector")],
            None,
            (Severity.HIGH, "detector"),
        ),
    ],
)
def test_resolve_severity_precedence(findings, level, expected):
    assert resolve_severity(findings, level) == expected


def test_segment_summary_applies_description_severity(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        visual, "qwen_enrichment_capability", lambda: SimpleNamespace(availability="available")
    )
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda *_args, **_kwargs: QwenSummary(
            ("violence",), "Visible injury.", severities={VIOLENCE: 2}
        ),
    )
    frames = [(tmp_path / "a.jpg", 0.0)]

    [confidence_based] = visual.summarize_flagged_segments(
        [_finding(0, Severity.HIGH, "confidence")], frames,
        summaries_per_segment=1, join_gap_seconds=0,
    )
    [detector_based] = visual.summarize_flagged_segments(
        [_finding(0, Severity.HIGH, "detector")], frames,
        summaries_per_segment=1, join_gap_seconds=0,
    )

    assert (confidence_based.severity, confidence_based.severity_source) == (
        Severity.MEDIUM,
        "description_model",
    )
    assert (detector_based.severity, detector_based.severity_source) == (
        Severity.HIGH,
        "detector",
    )


def test_unavailable_description_model_keeps_detector_severity(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        visual,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(
            availability="unavailable", enrichment_behavior="detector_findings_only"
        ),
    )
    findings = [_finding(0, Severity.LOW, "detector")]

    assert visual.summarize_flagged_segments(
        findings, [(tmp_path / "a.jpg", 0.0)], summaries_per_segment=1, join_gap_seconds=0
    ) == findings


def _described(monkeypatch, tmp_path, reply: str):
    from scanner.pipeline import qwen_summary

    monkeypatch.setattr(
        visual, "qwen_enrichment_capability", lambda: SimpleNamespace(availability="available")
    )
    monkeypatch.setattr(qwen_summary, "require_available", lambda _backend: None)
    monkeypatch.setattr(qwen_summary, "_load_model", lambda _model: (object(),) * 3)
    dependencies = {
        "mlx_vlm": SimpleNamespace(generate=lambda *_a, **_k: SimpleNamespace(text=reply)),
        "mlx_vlm.prompt_utils": SimpleNamespace(apply_chat_template=lambda *_a, **_k: "prompt"),
    }
    monkeypatch.setattr(qwen_summary, "_require_dependency", lambda name, _hint: dependencies[name])
    detected = _finding(0, Severity.HIGH, "confidence").model_copy(
        update={"details": "Detected d1_violence at 0.0s"}
    )
    return visual.summarize_flagged_segments(
        [detected], [(tmp_path / "a.jpg", 0.0)], summaries_per_segment=1, join_gap_seconds=0
    )


def test_invalid_description_reply_leaves_no_description_but_keeps_its_severity(
    monkeypatch, tmp_path: Path
):
    reply = (
        '{"version": 1, "objectionable": true, "summary": "Blood.", "classifications": '
        '[{"family": "violence", "subcategory": "bloodshed", "confidence": 0.9, '
        '"evidence": "Blood.", "severity": 1}]}'
    )

    [finding] = _described(monkeypatch, tmp_path, reply)

    assert finding.details is None
    assert (finding.severity, finding.severity_source) == (Severity.LOW, "description_model")
    persisted = finding.model_dump_json()
    assert "classifications" not in persisted and "Detected d1_violence" not in persisted


def test_valid_description_reply_is_unchanged(monkeypatch, tmp_path: Path):
    reply = (
        '{"version": 1, "objectionable": true, "summary": "A punch to the face.", '
        '"classifications": [{"family": "violence", "subcategory": "punching", '
        '"confidence": 0.9, "evidence": "A punch.", "severity": 1}]}'
    )

    [finding] = _described(monkeypatch, tmp_path, reply)

    assert finding.details == "A punch to the face."
