"""`d1-3b-sev`: d1-3B family decisions plus severity `score` questions in one request.

The four noul questions match `d1-3b-full4`; each severity key adds a `score` question
whose levels are the shipped severity rubric (lowest first). Severity is the
argmax level; the probability-weighted level is kept as `severity_expected`. A key
whose distribution is unusable (production's `d1_detector.severity_level` rules) gets
no level, so describe-eval counts it as unusable instead of scoring level 0.
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
    """(argmax level, expected level) for each key with a usable `score` answer.

    Answers may list probabilities or map `"0"`..`"n-1"` to them; anything production's
    `severity_level` rejects (missing, wrong length, non-finite, negative or all-zero)
    leaves the key out rather than failing the frame or defaulting to level 0.
    """
    answers = body.get("answers") if isinstance(body, dict) else None
    answers = answers if isinstance(answers, dict) else {}
    levels: dict[str, int] = {}
    expected: dict[str, float] = {}
    for key, ladder in RUBRIC.items():
        answer = answers.get(f"sev_{key}")
        probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
        if isinstance(probabilities, list):
            answer = {"probabilities": {str(i): value for i, value in enumerate(probabilities)}}
        level = d1_detector.severity_level(answer, len(ladder))
        if level is None:
            continue
        values = [float(answer["probabilities"][str(i)]) for i in range(len(ladder))]
        levels[key] = level
        expected[key] = round(sum(i * value for i, value in enumerate(values)) / sum(values), 4)
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
