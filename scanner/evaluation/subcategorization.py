"""Registry-safe classification results for AI-generated findings."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum

from scanner.models import (
    MCF_CATEGORY_REGISTRY,
    MCF_CATEGORY_TO_FAMILY,
    McfCategory,
    McfCategoryFamily,
)

SUBCATEGORIZATION_RESULT_VERSION = 1
MAX_QWEN_RESPONSE_CODEPOINTS = 8_192

SUBCATEGORIZATION_FALLBACKS = {
    family: McfCategory(categories[0][0])
    for family, categories in MCF_CATEGORY_REGISTRY.items()
}
SUBCATEGORIZATION_FALLBACKS[McfCategoryFamily.LANGUAGE] = McfCategory("swearing")

_DETERMINISTIC_LABEL_MAPPINGS = {
    ("nudenet", McfCategoryFamily.NUDITY, "BUTTOCKS_EXPOSED"): McfCategory(
        "bareButtocks"
    ),
    ("nudenet", McfCategoryFamily.NUDITY, "FEMALE_GENITALIA_EXPOSED"): McfCategory(
        "exposedGenitalia"
    ),
    ("nudenet", McfCategoryFamily.NUDITY, "MALE_GENITALIA_EXPOSED"): McfCategory(
        "exposedGenitalia"
    ),
}


class SubcategorizationMethod(str, Enum):
    GENERAL_FALLBACK = "general_fallback"
    DETERMINISTIC_LABEL = "deterministic_label"
    QWEN_SUMMARY = "qwen_summary"


class SubcategorizationFallbackReason(str, Enum):
    GENERAL_ONLY_POLICY = "general_only_policy"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INVALID_OUTPUT = "invalid_output"
    OUT_OF_FAMILY = "out_of_family"
    UNSUPPORTED_LABEL = "unsupported_label"
    MODEL_SELECTED_FALLBACK = "model_selected_fallback"


@dataclass(frozen=True)
class SubcategorizationResult:
    """One validated specific classification or successful general fallback."""

    category: McfCategoryFamily
    subcategory: McfCategory
    method: SubcategorizationMethod
    producer: str
    confidence: float | None = None
    decision_evidence: tuple[str, ...] = ()
    fallback_reason: SubcategorizationFallbackReason | None = None
    model_id: str | None = None
    model_revision: str | None = None
    version: int = SUBCATEGORIZATION_RESULT_VERSION

    def __post_init__(self) -> None:
        """Validate and normalize a subcategorization result after construction."""
        if self.version != SUBCATEGORIZATION_RESULT_VERSION:
            raise ValueError("Unsupported subcategorization result version")
        if not self.producer.strip():
            raise ValueError("Subcategorization producer is required")
        if MCF_CATEGORY_TO_FAMILY[self.subcategory.value] != self.category:
            raise ValueError("Subcategory does not belong to category")
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("Subcategorization confidence must be between 0 and 1")
        if self.method == SubcategorizationMethod.QWEN_SUMMARY and (
            not self.model_id or not self.model_revision
        ):
            raise ValueError("Model-backed subcategorization requires model provenance")

        is_general_fallback = self.subcategory == SUBCATEGORIZATION_FALLBACKS[self.category]
        if is_general_fallback != (self.fallback_reason is not None):
            raise ValueError(
                "General classifications require a fallback reason and specific ones forbid it"
            )


@dataclass(frozen=True)
class QwenSubcategorization:
    """Validated Qwen response plus one safe result for every requested family."""

    explanation: str
    objectionable: bool
    reported_subcategories: tuple[McfCategory, ...]
    results: tuple[SubcategorizationResult, ...]
    valid_output: bool


def _qwen_fallbacks(
    families: tuple[McfCategoryFamily, ...],
    reason: SubcategorizationFallbackReason,
    *,
    model_id: str,
    model_revision: str,
) -> tuple[SubcategorizationResult, ...]:
    """Build fallback subcategories for model output that lacks a valid match."""
    return tuple(
        SubcategorizationResult(
            category=family,
            subcategory=SUBCATEGORIZATION_FALLBACKS[family],
            method=SubcategorizationMethod.GENERAL_FALLBACK,
            producer="qwen_segment_summary",
            fallback_reason=reason,
            model_id=model_id,
            model_revision=model_revision,
        )
        for family in families
    )


def _invalid_qwen_output(
    response: str,
    families: tuple[McfCategoryFamily, ...],
    reason: SubcategorizationFallbackReason = SubcategorizationFallbackReason.INVALID_OUTPUT,
    *,
    model_id: str,
    model_revision: str,
) -> QwenSubcategorization:
    """Report an unusable Qwen classification with its safe fallback."""
    explanation = response.strip() or "No summary was returned by the classifier."
    return QwenSubcategorization(
        explanation=explanation,
        objectionable=True,
        reported_subcategories=(),
        results=_qwen_fallbacks(
            families,
            reason,
            model_id=model_id,
            model_revision=model_revision,
        ),
        valid_output=False,
    )


def subcategorize_qwen_response(
    response: str,
    families: tuple[McfCategoryFamily, ...],
    *,
    minimum_confidence: float | None = None,
    model_id: str = "mlx-community/Qwen3-VL-8B-Instruct-4bit",
    model_revision: str = "defcdea7cc7a4b0858fea563cbbce171d328e457",
) -> QwenSubcategorization:
    """Parse untrusted constrained output and apply an evaluated threshold if supplied."""
    expected_families = tuple(dict.fromkeys(families))
    if not expected_families:
        raise ValueError("At least one MCF family is required")
    if minimum_confidence is not None and (
        not math.isfinite(minimum_confidence) or not 0.0 <= minimum_confidence <= 1.0
    ):
        raise ValueError("Qwen confidence threshold must be between 0 and 1")
    if len(response) > MAX_QWEN_RESPONSE_CODEPOINTS:
        return _invalid_qwen_output(
            response[:MAX_QWEN_RESPONSE_CODEPOINTS],
            expected_families,
            model_id=model_id,
            model_revision=model_revision,
        )
    try:
        payload = json.loads(response)
    except (json.JSONDecodeError, TypeError):
        return _invalid_qwen_output(
            response,
            expected_families,
            model_id=model_id,
            model_revision=model_revision,
        )
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "objectionable",
        "summary",
        "classifications",
    }:
        return _invalid_qwen_output(
            response,
            expected_families,
            model_id=model_id,
            model_revision=model_revision,
        )
    summary = payload["summary"]
    objectionable = payload["objectionable"]
    classifications = payload["classifications"]
    if (
        isinstance(payload["version"], bool)
        or payload["version"] != SUBCATEGORIZATION_RESULT_VERSION
        or not isinstance(objectionable, bool)
        or not isinstance(summary, str)
        or not summary.strip()
        or len(summary) > 2_000
        or not isinstance(classifications, list)
    ):
        return _invalid_qwen_output(
            response,
            expected_families,
            model_id=model_id,
            model_revision=model_revision,
        )
    if not objectionable:
        if classifications:
            return _invalid_qwen_output(
                response,
                expected_families,
                model_id=model_id,
                model_revision=model_revision,
            )
        return QwenSubcategorization(
            explanation=summary.strip(),
            objectionable=False,
            reported_subcategories=(),
            results=_qwen_fallbacks(
                expected_families,
                SubcategorizationFallbackReason.INSUFFICIENT_EVIDENCE,
                model_id=model_id,
                model_revision=model_revision,
            ),
            valid_output=True,
        )
    if len(classifications) != len(expected_families):
        return _invalid_qwen_output(
            response,
            expected_families,
            model_id=model_id,
            model_revision=model_revision,
        )

    candidates: dict[McfCategoryFamily, tuple[McfCategory, float, str]] = {}
    for candidate in classifications:
        if not isinstance(candidate, dict) or set(candidate) != {
            "family",
            "subcategory",
            "confidence",
            "evidence",
        }:
            return _invalid_qwen_output(
                response,
                expected_families,
                model_id=model_id,
                model_revision=model_revision,
            )
        confidence = candidate["confidence"]
        evidence = candidate["evidence"]
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
            or not isinstance(evidence, str)
            or not evidence.strip()
            or len(evidence) > 1_000
        ):
            return _invalid_qwen_output(
                response,
                expected_families,
                model_id=model_id,
                model_revision=model_revision,
            )
        try:
            family = McfCategoryFamily(candidate["family"])
            subcategory = McfCategory(candidate["subcategory"])
        except (TypeError, ValueError):
            return _invalid_qwen_output(
                response,
                expected_families,
                model_id=model_id,
                model_revision=model_revision,
            )
        if family not in expected_families or family in candidates:
            return _invalid_qwen_output(
                response,
                expected_families,
                SubcategorizationFallbackReason.OUT_OF_FAMILY,
                model_id=model_id,
                model_revision=model_revision,
            )
        if MCF_CATEGORY_TO_FAMILY[subcategory.value] != family:
            return _invalid_qwen_output(
                response,
                expected_families,
                SubcategorizationFallbackReason.OUT_OF_FAMILY,
                model_id=model_id,
                model_revision=model_revision,
            )
        candidates[family] = (subcategory, float(confidence), evidence.strip())
    if set(candidates) != set(expected_families):
        return _invalid_qwen_output(
            response,
            expected_families,
            model_id=model_id,
            model_revision=model_revision,
        )

    results = []
    for family in expected_families:
        subcategory, confidence, evidence = candidates[family]
        fallback = SUBCATEGORIZATION_FALLBACKS[family]
        if subcategory == fallback:
            reason = SubcategorizationFallbackReason.MODEL_SELECTED_FALLBACK
        elif minimum_confidence is None or confidence < minimum_confidence:
            reason = SubcategorizationFallbackReason.INSUFFICIENT_EVIDENCE
        else:
            results.append(
                SubcategorizationResult(
                    category=family,
                    subcategory=subcategory,
                    method=SubcategorizationMethod.QWEN_SUMMARY,
                    producer="qwen_segment_summary",
                    confidence=confidence,
                    decision_evidence=(evidence,),
                    model_id=model_id,
                    model_revision=model_revision,
                )
            )
            continue
        results.append(
            SubcategorizationResult(
                category=family,
                subcategory=fallback,
                method=SubcategorizationMethod.GENERAL_FALLBACK,
                producer="qwen_segment_summary",
                confidence=confidence,
                decision_evidence=(evidence,),
                fallback_reason=reason,
                model_id=model_id,
                model_revision=model_revision,
            )
        )
    return QwenSubcategorization(
        explanation=summary.strip(),
        objectionable=True,
        reported_subcategories=tuple(
            candidates[family][0] for family in expected_families
        ),
        results=tuple(results),
        valid_output=True,
    )


def subcategorize_detector_label(
    *,
    producer: str,
    category: McfCategoryFamily,
    label: str,
    confidence: float,
    model_id: str | None = None,
    model_revision: str | None = None,
) -> SubcategorizationResult:
    """Apply only exact, documented detector-label mappings."""
    subcategory = _DETERMINISTIC_LABEL_MAPPINGS.get((producer, category, label))
    if subcategory is None:
        return SubcategorizationResult(
            category=category,
            subcategory=SUBCATEGORIZATION_FALLBACKS[category],
            method=SubcategorizationMethod.GENERAL_FALLBACK,
            producer=producer,
            confidence=confidence,
            decision_evidence=(f"label={label}",),
            fallback_reason=SubcategorizationFallbackReason.UNSUPPORTED_LABEL,
            model_id=model_id,
            model_revision=model_revision,
        )
    return SubcategorizationResult(
        category=category,
        subcategory=subcategory,
        method=SubcategorizationMethod.DETERMINISTIC_LABEL,
        producer=producer,
        confidence=confidence,
        decision_evidence=(f"label={label}",),
        model_id=model_id,
        model_revision=model_revision,
    )
