"""`d1-3b-sev`: d1-3B family decisions plus severity `score` questions in one request.

The four noul questions match `d1-3b-full4`; each severity key adds a `score` question
whose levels are the shipped severity rubric (lowest first). Severity is the
argmax level; the probability-weighted level is kept as `severity_expected`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scanner.pipeline import d1_detector

from ..describe import RUBRIC
from .llama_backend import LlamaServerDecisionBackend, image_long_edge
from .schema import build_request, image_data_url, parse_response


def severity_questions() -> dict[str, dict]:
    """The shipped d1-3B detector's `score` questions, keyed `sev_<key>`."""
    return {key: value for key, value in d1_detector.questions().items() if key.startswith("sev_")}


def parse_severity(body: dict) -> tuple[dict[str, int], dict[str, float]]:
    """(argmax level, expected level) per key from `score` answers; raises ValueError."""
    answers = body.get("answers") or {}
    levels: dict[str, int] = {}
    expected: dict[str, float] = {}
    for key, ladder in RUBRIC.items():
        answer = answers.get(f"sev_{key}")
        probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
        if isinstance(probabilities, dict):
            probabilities = [probabilities.get(str(level), 0.0) for level in range(len(ladder))]
        if not isinstance(probabilities, list) or len(probabilities) != len(ladder):
            raise ValueError(f"missing score answer for {key}: {answer!r}")
        values = [float(value) for value in probabilities]
        levels[key] = max(range(len(values)), key=values.__getitem__)
        total = sum(values) or 1.0
        expected[key] = round(sum(level * value for level, value in enumerate(values)) / total, 4)
    return levels, expected


class D1SeverityBackend(LlamaServerDecisionBackend):
    """d1-3b-full4 plus four severity score questions in the same request."""

    def _score_frame(self, path: Path) -> dict:
        url = image_data_url(path, image_long_edge(self.spec))
        body = build_request(url, self.questions, self.spec.state)
        body["questions"].update(severity_questions())
        response = self._post(body)
        scores = parse_response(response, self.questions)
        severity, expected = parse_severity(response)
        return {
            "scores": scores,
            "labels": [],
            "asked": sorted(scores),
            "severity": severity,
            "severity_expected": expected,
        }

    def meta(self) -> dict[str, Any]:
        meta = super().meta()
        meta["notes"] += " Adds severity score questions (rubric levels); argmax level kept."
        return meta
