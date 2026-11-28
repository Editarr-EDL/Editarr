"""Description + severity turn shared by the generative VLM backends.

The rubric is the shipped `scanner.evaluation.severity_rubric` (also used by the d1
`score` questions), so every model rates against identical wording; the ground-truth
ladders in `severity_map.json` follow the same levels.
"""

from __future__ import annotations

import json
from typing import Any

from scanner.evaluation import severity_rubric

from .severity import SEVERITY_KEYS

MAX_DESCRIPTION_WORDS = 60
DESCRIPTION_TOKENS = 120
# The shipped severity rubric, keyed by family name.
RUBRIC: dict[str, tuple[str, ...]] = {
    family.value: levels for family, levels in severity_rubric.RUBRIC.items()
}
assert tuple(RUBRIC) == SEVERITY_KEYS

DESCRIBE_PROMPT = (
    "Reply with JSON only, in this form: "
    '{"description": "<what objectionable content is visible, in at most 60 words, '
    "or 'none'>\", "
    '"severity": {"violence": 0-3, "sex": 0-3, "nudity": 0-3, "drugs": 0-2}}. '
    "Severity levels: "
    + " ".join(
        f"{key}: " + "; ".join(f"{level} = {text}" for level, text in enumerate(levels)) + "."
        for key, levels in RUBRIC.items()
    )
)
# Used by the cascade, which only describes frames d1 already flagged: an explicit
# "reply none" escape made Qwen3-VL answer "none" for 20 of 21 flagged frames.
DESCRIPTION_ONLY_PROMPT = (
    "In at most 60 words, describe what is happening in this image, including any "
    "violence, blood or gore, sexual content, nudity, or drug, alcohol or tobacco use."
)

DESCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "maxLength": 400},
        "severity": {
            "type": "object",
            "properties": {
                key: {"type": "integer", "enum": list(range(len(levels)))}
                for key, levels in RUBRIC.items()
            },
            "required": list(RUBRIC),
            "additionalProperties": False,
        },
    },
    "required": ["description", "severity"],
    "additionalProperties": False,
}


def clip_words(text: str, limit: int = MAX_DESCRIPTION_WORDS) -> str:
    words = text.split()
    return " ".join(words[:limit]) + ("…" if len(words) > limit else "")


def parse_description(text: str) -> tuple[str, dict[str, int]]:
    """Validate the JSON reply; returns (description, severity) or raises ValueError.

    Severity keys with an out-of-range level are left out (unusable), not clamped.
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    payload = json.loads(text[start : end + 1])
    description = payload.get("description")
    severity = payload.get("severity")
    if not isinstance(description, str) or not isinstance(severity, dict):
        raise ValueError("reply lacks description or severity")
    levels: dict[str, int] = {}
    for key, ladder in RUBRIC.items():
        value = severity.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"severity.{key} is not an integer")
        if 0 <= value < len(ladder):  # an out-of-range level is unusable, never clamped
            levels[key] = value
    return clip_words(description.strip()) or "none", levels


def no_content() -> tuple[str, dict[str, int]]:
    """Result used when the conditional variants skip the description turn."""
    return "none", {key: 0 for key in RUBRIC}


def is_none_description(text: str | None) -> bool:
    return text is not None and text.strip().strip(".").lower() in {"none", "no", ""}
