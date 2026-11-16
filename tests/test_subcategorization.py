import json

import pytest

from scanner.evaluation.subcategorization import (
    SUBCATEGORIZATION_RESULT_VERSION,
    SubcategorizationFallbackReason,
    SubcategorizationMethod,
    SubcategorizationResult,
    subcategorize_detector_label,
    subcategorize_qwen_response,
)
from scanner.models import McfCategory, McfCategoryFamily


def test_subcategorization_result_records_specific_model_provenance():
    result = SubcategorizationResult(
        category=McfCategoryFamily.NUDITY,
        subcategory=McfCategory("bareButtocks"),
        method=SubcategorizationMethod.QWEN_SUMMARY,
        producer="qwen_segment_summary",
        model_id="mlx-community/Qwen3-VL-8B-Instruct-4bit",
        model_revision="managed-artifact-v1",
        confidence=0.92,
        decision_evidence=("bare buttocks are visibly exposed",),
    )

    assert result.version == SUBCATEGORIZATION_RESULT_VERSION
    assert result.fallback_reason is None


def test_subcategorization_result_treats_general_classification_as_successful_fallback():
    result = SubcategorizationResult(
        category=McfCategoryFamily.VIOLENCE,
        subcategory=McfCategory("violence"),
        method=SubcategorizationMethod.GENERAL_FALLBACK,
        producer="siglip2_moderation_violence",
        confidence=0.81,
        fallback_reason=SubcategorizationFallbackReason.GENERAL_ONLY_POLICY,
    )

    assert result.subcategory.value == "violence"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"subcategory": McfCategory("swearing")},
            "Subcategory does not belong to category",
        ),
        (
            {"confidence": float("nan")},
            "confidence must be between 0 and 1",
        ),
        (
            {"fallback_reason": None},
            "General classifications require a fallback reason",
        ),
    ],
)
def test_subcategorization_result_rejects_invalid_fallbacks(overrides, message):
    values = {
        "category": McfCategoryFamily.NUDITY,
        "subcategory": McfCategory("nudity"),
        "method": SubcategorizationMethod.GENERAL_FALLBACK,
        "producer": "nudenet",
        "confidence": 0.8,
        "fallback_reason": SubcategorizationFallbackReason.GENERAL_ONLY_POLICY,
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        SubcategorizationResult(**values)


def test_subcategorization_result_requires_qwen_model_provenance():
    with pytest.raises(ValueError, match="requires model provenance"):
        SubcategorizationResult(
            category=McfCategoryFamily.NUDITY,
            subcategory=McfCategory("toplessness"),
            method=SubcategorizationMethod.QWEN_SUMMARY,
            producer="qwen_segment_summary",
        )


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("BUTTOCKS_EXPOSED", "bareButtocks"),
        ("FEMALE_GENITALIA_EXPOSED", "exposedGenitalia"),
        ("MALE_GENITALIA_EXPOSED", "exposedGenitalia"),
    ],
)
def test_nudenet_exact_labels_map_to_specific_subcategories(label, expected):
    result = subcategorize_detector_label(
        producer="nudenet",
        category=McfCategoryFamily.NUDITY,
        label=label,
        confidence=0.9,
    )

    assert result.subcategory == McfCategory(expected)
    assert result.method == SubcategorizationMethod.DETERMINISTIC_LABEL
    assert result.fallback_reason is None


@pytest.mark.parametrize("label", ["FEMALE_BREAST_EXPOSED", "ANUS_EXPOSED"])
def test_nudenet_ambiguous_labels_keep_the_nudity_fallback(label):
    result = subcategorize_detector_label(
        producer="nudenet",
        category=McfCategoryFamily.NUDITY,
        label=label,
        confidence=0.9,
    )

    assert result.subcategory == McfCategory("nudity")
    assert result.method == SubcategorizationMethod.GENERAL_FALLBACK
    assert result.fallback_reason == SubcategorizationFallbackReason.UNSUPPORTED_LABEL


def test_language_fallback_is_swearing():
    result = subcategorize_detector_label(
        producer="better_profanity",
        category=McfCategoryFamily.LANGUAGE,
        label="detected-token",
        confidence=0.9,
    )

    assert result.subcategory == McfCategory("swearing")
    assert result.fallback_reason == SubcategorizationFallbackReason.UNSUPPORTED_LABEL


def _qwen_response(*classifications, objectionable=True):
    return json.dumps(
        {
            "version": 1,
            "objectionable": objectionable,
            "summary": "Visible content summary.",
            "classifications": list(classifications),
        }
    )


def _qwen_classification(family, subcategory, confidence=0.95):
    return {
        "family": family,
        "subcategory": subcategory,
        "confidence": confidence,
        "evidence": "Visible evidence supports this exact category.",
    }


def test_qwen_specific_result_requires_an_explicit_evaluated_threshold():
    response = _qwen_response(
        _qwen_classification("nudity", "bareButtocks", confidence=0.94)
    )

    unevaluated = subcategorize_qwen_response(
        response, (McfCategoryFamily.NUDITY,)
    )
    accepted = subcategorize_qwen_response(
        response,
        (McfCategoryFamily.NUDITY,),
        minimum_confidence=0.9,
    )

    assert unevaluated.valid_output is True
    assert unevaluated.reported_subcategories == (McfCategory("bareButtocks"),)
    assert unevaluated.results[0].subcategory == McfCategory("nudity")
    assert (
        unevaluated.results[0].fallback_reason
        == SubcategorizationFallbackReason.INSUFFICIENT_EVIDENCE
    )
    assert accepted.results[0].subcategory == McfCategory("bareButtocks")
    assert accepted.results[0].method == SubcategorizationMethod.QWEN_SUMMARY


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ("not JSON", SubcategorizationFallbackReason.INVALID_OUTPUT),
        (
            json.dumps(
                {
                    "version": True,
                    "objectionable": True,
                    "summary": "Visible content summary.",
                    "classifications": [
                        _qwen_classification("nudity", "bareButtocks")
                    ],
                }
            ),
            SubcategorizationFallbackReason.INVALID_OUTPUT,
        ),
        (
            _qwen_response(_qwen_classification("violence", "weapons")),
            SubcategorizationFallbackReason.OUT_OF_FAMILY,
        ),
        (
            _qwen_response(_qwen_classification("nudity", "weapons")),
            SubcategorizationFallbackReason.OUT_OF_FAMILY,
        ),
        (
            _qwen_response(
                _qwen_classification("nudity", "bareButtocks"),
                _qwen_classification("nudity", "exposedGenitalia"),
            ),
            SubcategorizationFallbackReason.INVALID_OUTPUT,
        ),
    ],
)
def test_qwen_malformed_or_out_of_family_output_fails_closed(response, reason):
    result = subcategorize_qwen_response(
        response, (McfCategoryFamily.NUDITY,)
    )

    assert result.valid_output is False
    assert result.results[0].subcategory == McfCategory("nudity")
    assert result.results[0].fallback_reason == reason


def test_qwen_clean_result_is_valid_and_keeps_detector_fallbacks():
    result = subcategorize_qwen_response(
        _qwen_response(objectionable=False),
        (McfCategoryFamily.NUDITY, McfCategoryFamily.VIOLENCE),
        minimum_confidence=0.9,
    )

    assert result.valid_output is True
    assert result.objectionable is False
    assert [item.subcategory.value for item in result.results] == ["nudity", "violence"]
    assert all(
        item.fallback_reason == SubcategorizationFallbackReason.INSUFFICIENT_EVIDENCE
        for item in result.results
    )


def test_qwen_response_requires_every_requested_family_once():
    result = subcategorize_qwen_response(
        _qwen_response(_qwen_classification("nudity", "nudity")),
        (McfCategoryFamily.NUDITY, McfCategoryFamily.VIOLENCE),
    )

    assert result.valid_output is False
    assert [item.subcategory.value for item in result.results] == ["nudity", "violence"]
