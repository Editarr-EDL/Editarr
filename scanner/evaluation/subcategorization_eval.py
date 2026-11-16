"""Evaluate constrained subcategorization responses against reviewed expectations."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scanner.models import MCF_CATEGORY_TO_FAMILY, McfCategory, McfCategoryFamily

from .subcategorization import (
    subcategorize_detector_label,
    subcategorize_qwen_response,
)


@dataclass(frozen=True)
class SubcategorizationCase:
    case_id: str
    families: tuple[McfCategoryFamily, ...]
    response: str
    expected: tuple[McfCategory, ...]
    model_latency_ms: float


@dataclass(frozen=True)
class SubcategorizationCorpus:
    corpus_id: str
    license: str
    purpose: str
    minimum_confidence: float
    cases: tuple[SubcategorizationCase, ...]


def load_subcategorization_corpus(path: Path) -> SubcategorizationCorpus:
    """Load a bounded, versioned corpus of reviewed constrained-model responses."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read subcategorization corpus: {path}") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "corpus_id",
        "license",
        "purpose",
        "minimum_confidence",
        "cases",
    }:
        raise ValueError("Subcategorization corpus has an invalid top-level schema")
    if payload["version"] != 1:
        raise ValueError("Unsupported subcategorization corpus version")
    for field in ("corpus_id", "license", "purpose"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"Subcategorization corpus {field} is required")
    threshold = payload["minimum_confidence"]
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or not 0.0 <= threshold <= 1.0
    ):
        raise ValueError("Subcategorization corpus threshold must be between 0 and 1")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases or len(raw_cases) > 10_000:
        raise ValueError("Subcategorization corpus must contain 1 to 10000 cases")

    cases = tuple(_load_case(item, index) for index, item in enumerate(raw_cases, start=1))
    case_ids = [item.case_id for item in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Subcategorization corpus case ids must be unique")
    return SubcategorizationCorpus(
        corpus_id=payload["corpus_id"].strip(),
        license=payload["license"].strip(),
        purpose=payload["purpose"].strip(),
        minimum_confidence=float(threshold),
        cases=cases,
    )


def _load_case(payload: object, index: int) -> SubcategorizationCase:
    """Read and validate one subcategorization evaluation fixture."""
    if not isinstance(payload, dict) or set(payload) != {
        "id",
        "families",
        "response",
        "expected",
        "model_latency_ms",
    }:
        raise ValueError(f"Subcategorization corpus case {index} has an invalid schema")
    case_id = payload["id"]
    response = payload["response"]
    raw_families = payload["families"]
    expected = payload["expected"]
    latency = payload["model_latency_ms"]
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError(f"Subcategorization corpus case {index} needs an id")
    if not isinstance(response, str):
        raise ValueError(f"Subcategorization corpus case {index} response must be text")
    if not isinstance(raw_families, list) or not raw_families:
        raise ValueError(f"Subcategorization corpus case {index} needs families")
    try:
        families = tuple(McfCategoryFamily(value) for value in raw_families)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Subcategorization corpus case {index} has an invalid family"
        ) from exc
    if len(families) != len(set(families)):
        raise ValueError(f"Subcategorization corpus case {index} repeats a family")
    if not isinstance(expected, dict) or set(expected) != {
        family.value for family in families
    }:
        raise ValueError(
            f"Subcategorization corpus case {index} expected values must match families"
        )
    try:
        expected_categories = tuple(McfCategory(expected[family.value]) for family in families)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Subcategorization corpus case {index} has an invalid expected category"
        ) from exc
    if any(
        MCF_CATEGORY_TO_FAMILY[subcategory.value] != family
        for family, subcategory in zip(families, expected_categories, strict=True)
    ):
        raise ValueError(
            f"Subcategorization corpus case {index} has an out-of-family expectation"
        )
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(latency)
        or latency < 0
    ):
        raise ValueError(
            f"Subcategorization corpus case {index} model latency must be non-negative"
        )
    return SubcategorizationCase(
        case_id=case_id.strip(),
        families=families,
        response=response,
        expected=expected_categories,
        model_latency_ms=float(latency),
    )


def _metric(true_positive: int, false_positive: int, false_negative: int) -> dict[str, Any]:
    """Compute one accuracy metric from expected and observed classifications."""
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else None
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else None
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall
        else 0.0
    )
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def evaluate_subcategorization_corpus(path: Path) -> dict[str, Any]:
    """Measure exact-token quality and fail-closed behavior for one corpus."""
    corpus = load_subcategorization_corpus(path)
    case_results = []
    expected_predictions: list[tuple[str, str]] = []
    fallback_count = 0
    invalid_output_count = 0
    family_regressions = 0
    total_results = 0
    for case in corpus.cases:
        parsed = subcategorize_qwen_response(
            case.response,
            case.families,
            minimum_confidence=corpus.minimum_confidence,
        )
        predicted = tuple(result.subcategory for result in parsed.results)
        if tuple(result.category for result in parsed.results) != case.families:
            family_regressions += 1
        if not parsed.valid_output:
            invalid_output_count += 1
        fallback_count += sum(result.fallback_reason is not None for result in parsed.results)
        total_results += len(parsed.results)
        expected_predictions.extend(
            (expected.value, actual.value)
            for expected, actual in zip(case.expected, predicted, strict=True)
        )
        case_results.append(
            {
                "id": case.case_id,
                "valid_output": parsed.valid_output,
                "expected": [value.value for value in case.expected],
                "predicted": [value.value for value in predicted],
                "fallback_reasons": [
                    result.fallback_reason.value if result.fallback_reason else None
                    for result in parsed.results
                ],
            }
        )

    tokens = sorted({value for pair in expected_predictions for value in pair})
    metrics = {}
    overall = [0, 0, 0]
    for token in tokens:
        true_positive = sum(
            expected == token and predicted == token
            for expected, predicted in expected_predictions
        )
        false_positive = sum(
            expected != token and predicted == token
            for expected, predicted in expected_predictions
        )
        false_negative = sum(
            expected == token and predicted != token
            for expected, predicted in expected_predictions
        )
        metrics[token] = _metric(true_positive, false_positive, false_negative)
        overall[0] += true_positive
        overall[1] += false_positive
        overall[2] += false_negative
    latencies = [case.model_latency_ms for case in corpus.cases]
    return {
        "corpus": {
            "id": corpus.corpus_id,
            "license": corpus.license,
            "purpose": corpus.purpose,
            "case_count": len(corpus.cases),
        },
        "minimum_confidence": corpus.minimum_confidence,
        "accuracy": {"overall": _metric(*overall), "by_subcategory": metrics},
        "general_fallback_rate": fallback_count / total_results,
        "invalid_output_rate": invalid_output_count / len(corpus.cases),
        "base_detection_family_regressions": family_regressions,
        "model_latency_ms": {
            "mean": sum(latencies) / len(latencies),
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies),
        },
        "cases": case_results,
    }


def evaluate_detector_mapping_corpus(path: Path) -> dict[str, Any]:
    """Measure deterministic producer-label mappings over a reviewed policy corpus."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read detector mapping corpus: {path}") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "corpus_id",
        "license",
        "purpose",
        "cases",
    }:
        raise ValueError("Detector mapping corpus has an invalid top-level schema")
    if payload["version"] != 1:
        raise ValueError("Unsupported detector mapping corpus version")
    for field in ("corpus_id", "license", "purpose"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"Detector mapping corpus {field} is required")
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not raw_cases or len(raw_cases) > 10_000:
        raise ValueError("Detector mapping corpus must contain 1 to 10000 cases")

    expected_predictions: list[tuple[str, str]] = []
    fallback_count = 0
    family_regressions = 0
    case_results = []
    case_ids: set[str] = set()
    for index, case in enumerate(raw_cases, start=1):
        if not isinstance(case, dict) or set(case) != {
            "id",
            "producer",
            "family",
            "label",
            "confidence",
            "expected_subcategory",
        }:
            raise ValueError(f"Detector mapping corpus case {index} has an invalid schema")
        case_id = case["id"]
        producer = case["producer"]
        label = case["label"]
        confidence = case["confidence"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in case_ids:
            raise ValueError(f"Detector mapping corpus case {index} needs a unique id")
        case_ids.add(case_id)
        if not isinstance(producer, str) or not producer.strip():
            raise ValueError(f"Detector mapping corpus case {index} needs a producer")
        if not isinstance(label, str) or not label:
            raise ValueError(f"Detector mapping corpus case {index} needs a label")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0.0 <= confidence <= 1.0
        ):
            raise ValueError(
                f"Detector mapping corpus case {index} confidence must be between 0 and 1"
            )
        try:
            family = McfCategoryFamily(case["family"])
            expected = McfCategory(case["expected_subcategory"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Detector mapping corpus case {index} has an invalid classification"
            ) from exc
        if MCF_CATEGORY_TO_FAMILY[expected.value] != family:
            raise ValueError(
                f"Detector mapping corpus case {index} has an out-of-family expectation"
            )

        result = subcategorize_detector_label(
            producer=producer,
            category=family,
            label=label,
            confidence=float(confidence),
        )
        expected_predictions.append((expected.value, result.subcategory.value))
        fallback_count += result.fallback_reason is not None
        family_regressions += result.category != family
        case_results.append(
            {
                "id": case_id,
                "expected": expected.value,
                "predicted": result.subcategory.value,
                "fallback_reason": (
                    result.fallback_reason.value if result.fallback_reason else None
                ),
            }
        )

    tokens = sorted({value for pair in expected_predictions for value in pair})
    metrics = {}
    overall = [0, 0, 0]
    for token in tokens:
        true_positive = sum(
            expected == token and predicted == token
            for expected, predicted in expected_predictions
        )
        false_positive = sum(
            expected != token and predicted == token
            for expected, predicted in expected_predictions
        )
        false_negative = sum(
            expected == token and predicted != token
            for expected, predicted in expected_predictions
        )
        metrics[token] = _metric(true_positive, false_positive, false_negative)
        overall[0] += true_positive
        overall[1] += false_positive
        overall[2] += false_negative
    return {
        "corpus": {
            "id": payload["corpus_id"],
            "license": payload["license"],
            "purpose": payload["purpose"],
            "case_count": len(raw_cases),
        },
        "accuracy": {"overall": _metric(*overall), "by_subcategory": metrics},
        "general_fallback_rate": fallback_count / len(raw_cases),
        "base_detection_family_regressions": family_regressions,
        "cases": case_results,
    }
