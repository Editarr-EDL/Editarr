"""Severity precedence for visual findings after the description step.

Each finding records where its severity came from in ``severity_source``:
``detector`` (a detector with its own severity questions, such as d1-3B),
``description_model`` (the deferred Qwen description step), or ``confidence`` (the
detector-confidence fallback mapping).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from scanner.evaluation.severity_rubric import level_severity
from scanner.models import FindingCreate, Severity

SeveritySource = Literal["detector", "description_model", "confidence"]
SEVERITY_DETECTOR: SeveritySource = "detector"
SEVERITY_DESCRIPTION_MODEL: SeveritySource = "description_model"
SEVERITY_CONFIDENCE: SeveritySource = "confidence"
_ORDER = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}


def highest(severities: Iterable[Severity | None]) -> Severity | None:
    """Return the most severe value, ignoring missing ones."""
    return max(
        (severity for severity in severities if severity),
        key=_ORDER.__getitem__,
        default=None,
    )


def resolve_severity(
    findings: list[FindingCreate], description_level: int | None
) -> tuple[Severity | None, SeveritySource | None]:
    """Choose one classification's severity: detector > description model > confidence.

    ``findings`` share one family within a segment. Detector severity wins whenever any
    finding carries it; otherwise a description-model level of at least 1 is used;
    otherwise the highest confidence-mapped severity remains.
    """
    detector = highest(
        finding.severity for finding in findings if finding.severity_source == SEVERITY_DETECTOR
    )
    if detector is not None:
        return detector, SEVERITY_DETECTOR
    if description_level is not None and description_level >= 1:
        return level_severity(description_level), SEVERITY_DESCRIPTION_MODEL
    fallback = highest(finding.severity for finding in findings)
    return fallback, SEVERITY_CONFIDENCE if fallback is not None else None
