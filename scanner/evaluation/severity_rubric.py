"""Shared severity rubric for decision detectors and the description model.

Levels are ordered lowest first. Level 0 means the family is absent; levels 1, 2 and 3
map to MCF ``low``, ``medium`` and ``high``. Drugs has no level 3, so its highest
severity is ``medium``.
"""

from __future__ import annotations

import json
import math

from scanner.models import McfCategoryFamily, Severity

RUBRIC: dict[McfCategoryFamily, tuple[str, ...]] = {
    McfCategoryFamily.VIOLENCE: (
        "no violence",
        "non-graphic violence: fighting or threats without visible injury or blood",
        "graphic violence: visible injury or blood",
        "gore: severe injury, mutilation or a lot of blood",
    ),
    McfCategoryFamily.SEX: (
        "no sexual content",
        "kissing",
        "sexual activity without nudity",
        "sexual activity with nudity, or sexual assault",
    ),
    McfCategoryFamily.NUDITY: (
        "no nudity",
        "immodest or revealing clothing",
        "non-sexual or implied nudity",
        "nudity during sexual activity",
    ),
    McfCategoryFamily.DRUGS: (
        "no drugs, alcohol or smoking",
        "legal drugs: alcohol, tobacco or medication",
        "illegal drugs",
    ),
}
_LEVEL_SEVERITY = {1: Severity.LOW, 2: Severity.MEDIUM, 3: Severity.HIGH}


def max_level(family: McfCategoryFamily) -> int | None:
    """Return the highest rubric level for ``family``, or None without a rubric."""
    levels = RUBRIC.get(family)
    return len(levels) - 1 if levels else None


def level_severity(level: int) -> Severity:
    """Map a rubric level to MCF severity; level 0 (or below) on a positive finding is low."""
    return _LEVEL_SEVERITY.get(min(max(level, 1), 3), Severity.LOW)


def parse_level(value: object, family: McfCategoryFamily) -> int | None:
    """Read an untrusted severity level for ``family``; None when it is unusable.

    Integers, integral floats and numeric strings are accepted. Values above the
    family's scale are clamped to its top level; negative or non-numeric values and
    families without a rubric yield None.
    """
    top = max_level(family)
    if top is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        value = int(value)
    if not isinstance(value, int) or value < 0:
        return None
    return min(value, top)


def rubric_prompt(families: tuple[McfCategoryFamily, ...]) -> str:
    """Describe the severity scale of each requested family that has one."""
    return " ".join(
        f"{family.value}: "
        + "; ".join(f"{level} = {text}" for level, text in enumerate(RUBRIC[family]))
        + "."
        for family in families
        if family in RUBRIC
    )


def extract_severities(
    response: str, families: tuple[McfCategoryFamily, ...]
) -> dict[McfCategoryFamily, int]:
    """Read per-family severity levels from a description-model JSON response.

    This is deliberately lenient and independent of subcategory validation: any
    classification object naming a requested family with a usable level counts, so a
    response with an unknown subcategory token still contributes its severity. Code
    fences are ignored. Unparseable responses yield no levels.
    """
    text = response.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    classifications = payload.get("classifications") if isinstance(payload, dict) else None
    if not isinstance(classifications, list) or payload.get("objectionable") is False:
        return {}
    levels: dict[McfCategoryFamily, int] = {}
    for item in classifications:
        if not isinstance(item, dict):
            continue
        try:
            family = McfCategoryFamily(item.get("family"))
        except (TypeError, ValueError):
            continue
        level = parse_level(item.get("severity"), family)
        if family in families and level is not None and family not in levels:
            levels[family] = level
    return levels
