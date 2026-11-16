import json
from pathlib import Path

import pytest

from scanner.credits.credits_detector import (
    FRAME_CACHE_VERSION,
    ChapterMarker,
    FrameObservation,
    QwenAssessment,
    credits_cache_key,
    default_configuration,
    detect_from_observations,
)
from scanner.models import CreditsConfidenceBand, CreditsDetectionMethod
from scanner.pipeline.processes import ScanCancelledError

CORPUS_PATH = Path(__file__).parent / "fixtures" / "mts" / "credits-evaluation-v1.json"


def _detect(case, *, qwen_assessments=()):
    return detect_from_observations(
        duration_seconds=case["duration_seconds"],
        configuration=default_configuration(),
        chapters=[ChapterMarker(**item) for item in case["chapters"]],
        scene_boundaries=case["scene_boundaries"],
        observations=[FrameObservation(**item) for item in case["observations"]],
        qwen_assessments=qwen_assessments,
    )


@pytest.mark.parametrize(
    "case",
    json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"],
    ids=lambda case: case["id"],
)
def test_detector_matches_the_generated_boundary_corpus(case):
    result = _detect(case)
    expected = case["expected_boundary_seconds"]

    if expected is None:
        assert result.outcome == "no_candidate"
        return
    assert result.outcome == "candidate"
    candidate = result.candidates[0]
    assert abs(candidate.boundary_seconds - expected) <= case["tolerance_seconds"]
    assert candidate.evidence == sorted(
        candidate.evidence,
        key=lambda evidence: (
            -abs(evidence.score),
            list(type(evidence.kind)).index(evidence.kind),
            evidence.timestamp_seconds,
        ),
    )


def test_ambiguous_candidate_is_returned_with_explicit_low_confidence():
    case = next(
        item
        for item in json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
        if item["id"] == "ambiguous-low-confidence"
    )

    candidate = _detect(case).candidates[0]

    assert candidate.confidence_band == CreditsConfidenceBand.LOW
    assert candidate.method == CreditsDetectionMethod.VISUAL_TEXT


def test_qwen_annotates_an_existing_candidate_but_cannot_create_one():
    assessment = QwenAssessment(70.0, 0.9, "Closing credits are visible")
    no_visual = detect_from_observations(
        duration_seconds=90.0,
        configuration=default_configuration(qwen_model="qwen3-vl-4b-instruct-4bit"),
        qwen_assessments=[assessment],
    )
    assert no_visual.outcome == "no_candidate"

    baseline = detect_from_observations(
        duration_seconds=90.0,
        configuration=default_configuration(),
        observations=[FrameObservation(70.0, 0.7, 0.72, 0.01)],
    )
    result = detect_from_observations(
        duration_seconds=90.0,
        configuration=default_configuration(qwen_model="qwen3-vl-4b-instruct-4bit"),
        observations=[FrameObservation(70.0, 0.7, 0.72, 0.01)],
        qwen_assessments=[assessment],
    )
    assert result.candidates[0].method == CreditsDetectionMethod.HYBRID_QWEN
    assert result.candidates[0].confidence >= baseline.candidates[0].confidence


def test_qwen_evidence_is_rejected_when_qwen_is_disabled():
    with pytest.raises(ValueError, match="requires an enabled Qwen model"):
        detect_from_observations(
            duration_seconds=90.0,
            configuration=default_configuration(),
            observations=[FrameObservation(70.0, 0.7, 0.72, 0.01)],
            qwen_assessments=[QwenAssessment(70.0, 0.9, "credits")],
        )


def test_unknown_detector_and_qwen_versions_are_rejected():
    configuration = default_configuration()
    with pytest.raises(ValueError, match="detector version"):
        detect_from_observations(
            duration_seconds=90.0,
            configuration=configuration.model_copy(update={"detector_version": "future"}),
        )
    with pytest.raises(ValueError, match="Qwen model"):
        credits_cache_key(
            video_hash="abc",
            configuration=configuration.model_copy(update={"qwen_model": "unpinned"}),
        )


def test_credible_chapter_outranks_a_later_post_credits_visual_candidate():
    result = detect_from_observations(
        duration_seconds=120.0,
        configuration=default_configuration(),
        chapters=[ChapterMarker(80.0, "End Credits")],
        observations=[
            FrameObservation(80.0, 0.7, 0.7, 0.01),
            FrameObservation(105.0, 1.0, 1.0, 0.01),
        ],
        scene_boundaries=[80.0, 105.0],
    )

    assert [candidate.boundary_seconds for candidate in result.candidates] == [80.0, 105.0]
    assert result.best_candidate_id == result.candidates[0].id


def test_evidence_at_exact_file_end_does_not_create_a_zero_length_candidate():
    result = detect_from_observations(
        duration_seconds=90.0,
        configuration=default_configuration(),
        chapters=[ChapterMarker(90.0, "End Credits")],
        observations=[FrameObservation(90.0, 1.0, 1.0, 0.0)],
    )

    assert result.outcome == "no_candidate"


def test_chapter_just_before_file_end_keeps_candidate_inside_runtime():
    result = detect_from_observations(
        duration_seconds=90.0,
        configuration=default_configuration(),
        chapters=[ChapterMarker(89.9995, "End Credits")],
    )

    assert result.outcome == "candidate"
    assert result.candidates[0].boundary_seconds == 89.9995
    assert result.candidates[0].range_end_seconds == 90.0


def test_cache_key_changes_for_every_versioned_behavior_input():
    configuration = default_configuration()
    baseline = credits_cache_key(video_hash="abc", configuration=configuration)

    assert baseline == credits_cache_key(video_hash="abc", configuration=configuration)
    assert baseline != credits_cache_key(video_hash="def", configuration=configuration)
    assert baseline != credits_cache_key(
        video_hash="abc",
        configuration=configuration.model_copy(update={"tail_window_seconds": 60.0}),
    )
    assert baseline != credits_cache_key(
        video_hash="abc",
        configuration=configuration,
        frame_cache_version=FRAME_CACHE_VERSION + "-changed",
    )
    assert baseline != credits_cache_key(
        video_hash="abc",
        configuration=default_configuration(qwen_model="qwen3-vl-4b-instruct-4bit"),
    )


def test_detector_honors_cancellation_hook():
    with pytest.raises(ScanCancelledError):
        detect_from_observations(
            duration_seconds=90.0,
            configuration=default_configuration(),
            chapters=[ChapterMarker(70.0, "End Credits")],
            should_cancel=lambda: True,
        )


@pytest.mark.parametrize("scene_boundary", [float("nan"), -1.0, 91.0])
def test_detector_rejects_invalid_scene_boundaries(scene_boundary):
    with pytest.raises(ValueError, match="Scene boundary"):
        detect_from_observations(
            duration_seconds=90.0,
            configuration=default_configuration(),
            scene_boundaries=[scene_boundary],
        )
