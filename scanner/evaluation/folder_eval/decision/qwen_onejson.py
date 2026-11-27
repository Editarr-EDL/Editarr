"""One-turn JSON variants: decisions, severity and description in a single answer.

The frame and one instruction go in a single user turn; the model answers with one JSON
object whose field order is fixed (`ORDERS`). Decision scores are read from the
generated token where each boolean value starts: P(true) / (P(true) + P(false)) over
that position's top log-probabilities. When the alternative is not among them the score
falls back to 1.0/0.0 from the generated value and the key is listed in
`logprob_fallback`. llama.cpp enforces the order with a JSON schema grammar; MLX has no
grammar, so its reply is validated and retried once.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ..describe import DESCRIBE_SCHEMA, RUBRIC, clip_words
from .schema import FAMILY_SUBJECTS

BOOLEAN_KEYS = {
    "drugs": "drugs",
    "nudity": "nudity",
    "sex": "sex",
    "violence": "violence",
    "blood_gore": "fear/hemophobia",
}
BOOLEAN_SUBJECTS = {**FAMILY_SUBJECTS, "blood_gore": "blood or gore"}
ORDERS = {
    "decide-first": (*BOOLEAN_KEYS, "severity", "description"),
    "describe-first": ("description", *BOOLEAN_KEYS, "severity"),
}
ONEJSON_TOKENS = 160


def onejson_schema(order: str) -> dict[str, Any]:
    """JSON schema with properties (and hence grammar order) in `ORDERS[order]`."""
    fields = {key: {"type": "boolean"} for key in BOOLEAN_KEYS}
    fields["severity"] = DESCRIBE_SCHEMA["properties"]["severity"]
    fields["description"] = DESCRIBE_SCHEMA["properties"]["description"]
    keys = ORDERS[order]
    return {
        "type": "object",
        "properties": {key: fields[key] for key in keys},
        "required": list(keys),
        "additionalProperties": False,
    }


def onejson_prompt(order: str) -> str:
    """Single instruction listing the fields in `order`, with the shared severity rubric."""
    shapes = {key: "true or false" for key in BOOLEAN_KEYS}
    shapes["severity"] = '{"violence": 0-3, "sex": 0-3, "nudity": 0-3, "drugs": 0-2}'
    shapes["description"] = (
        "\"<what objectionable content is visible, in at most 60 words, or 'none'>\""
    )
    form = "{" + ", ".join(f'"{key}": {shapes[key]}' for key in ORDERS[order]) + "}"
    booleans = "; ".join(
        f"{key}: true if this video frame shows {subject}"
        for key, subject in ((k, BOOLEAN_SUBJECTS[k]) for k in BOOLEAN_KEYS)
    )
    levels = " ".join(
        f"{key}: " + "; ".join(f"{level} = {text}" for level, text in enumerate(ladder)) + "."
        for key, ladder in RUBRIC.items()
    )
    return (
        f"Reply with JSON only, with the fields in this order: {form}. {booleans}. "
        f"Severity levels: {levels}"
    )


def _classify(token: str) -> str | None:
    word = token.strip().lstrip(":").strip().lower()
    if word.startswith("true"):
        return "true"
    if word.startswith("false"):
        return "false"
    return None


def boolean_probabilities(
    tokens: list[tuple[str, list[dict]]],
) -> tuple[dict[str, float], list[str], dict[str, bool]]:
    """Scores per MCF key from (token text, top log-probabilities) generation steps.

    Returns (scores, keys that fell back to the generated value, generated booleans).
    Keys whose value position is not found are missing from all three.
    """
    text = "".join(token for token, _top in tokens)
    ends, position = [], 0
    for token, _top in tokens:
        position += len(token)
        ends.append(position)
    scores: dict[str, float] = {}
    fallback: list[str] = []
    values: dict[str, bool] = {}
    for field, key in BOOLEAN_KEYS.items():
        match = re.search(rf'"{field}"\s*:\s*(true|false)', text)
        if not match:
            continue
        start = match.start(1)
        index = next(i for i, end in enumerate(ends) if end > start)
        totals = {"true": 0.0, "false": 0.0}
        for candidate in tokens[index][1]:
            word = _classify(str(candidate.get("token", "")))
            if word:
                totals[word] += math.exp(float(candidate["logprob"]))
        generated = match.group(1) == "true"
        values[key] = generated
        if totals["true"] > 0 and totals["false"] > 0:
            scores[key] = totals["true"] / (totals["true"] + totals["false"])
        else:
            scores[key] = 1.0 if generated else 0.0
            fallback.append(key)
    return scores, fallback, values


def parse_onejson(text: str) -> tuple[str, dict[str, int]]:
    """Validate a one-turn reply; returns (description, severity) or raises ValueError."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("reply is not an object")
    for field in BOOLEAN_KEYS:
        if not isinstance(payload.get(field), bool):
            raise ValueError(f"{field} is not a boolean")
    description = payload.get("description")
    severity = payload.get("severity")
    if not isinstance(description, str) or not isinstance(severity, dict):
        raise ValueError("reply lacks description or severity")
    levels: dict[str, int] = {}
    for key, ladder in RUBRIC.items():
        value = severity.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"severity.{key} is not an integer")
        levels[key] = min(max(value, 0), len(ladder) - 1)
    return clip_words(description.strip()) or "none", levels


def frame_from_reply(tokens: list[tuple[str, list[dict]]], status: str = "ok") -> dict[str, Any]:
    """Frame dict (scores, severity, description, fallbacks) from one generated reply."""
    text = "".join(token for token, _top in tokens)
    scores, fallback, _values = boolean_probabilities(tokens)
    missing = [key for key in BOOLEAN_KEYS.values() if key not in scores]
    frame: dict[str, Any] = {
        "scores": {**dict.fromkeys(missing, 0.0), **scores},
        "labels": [],
        "logprob_fallback": sorted(fallback),
    }
    frame["asked"] = sorted(frame["scores"])
    try:
        description, severity = parse_onejson(text)
    except (ValueError, TypeError):
        frame.update(description=None, severity=None, describe="failed")
        return frame
    frame.update(description=description, severity=severity, describe=status)
    return frame
