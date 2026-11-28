"""Severity ground truth for film windows and agreement metrics.

Levels come from each window's `source_tags` (the film tags overlapping it, padded like
the film labels) through the editable ladders in `severity_map.json`; a key's level is
the max over its tags. A key whose family the window's labels mark present, but none of
whose tags has a severity level (e.g. `violence/disturbing_images`), is unscored (None)
rather than 0. Reviewer corrections override presence: a family corrected absent is
level 0, and a family (or one of its tokens) corrected present without a severity tag
is unscored (None). Correcting only a token absent leaves the level as is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .labels import family_of

DEFAULT_SEVERITY_MAP_PATH = Path(__file__).with_name("severity_map.json")
SEVERITY_KEYS = ("violence", "sex", "nudity", "drugs")


class SeverityMap:
    """Tag → level ladders per key, with per-key ambiguous tags."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.levels: dict[str, dict[str, int]] = {
            key: {tag: int(level) for tag, level in ladder.items()}
            for key, ladder in payload["levels"].items()
        }
        self.max_level = {key: int(value) for key, value in payload["max_level"].items()}
        self.ambiguous = {
            key: frozenset(tags) for key, tags in payload.get("ambiguous", {}).items()
        }
        for key, ladder in self.levels.items():
            if key not in self.max_level or any(
                not 0 <= level <= self.max_level[key] for level in ladder.values()
            ):
                raise ValueError(f"severity map: {key} levels must lie in 0..max_level")

    @classmethod
    def load(cls, path: Path = DEFAULT_SEVERITY_MAP_PATH) -> SeverityMap:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def from_tags(self, tags: list[str]) -> dict[str, int | None]:
        """Level per key from tag names; None where an ambiguous tag overlaps."""
        names = set(tags)
        result: dict[str, int | None] = {}
        for key, ladder in self.levels.items():
            if names & self.ambiguous.get(key, frozenset()):
                result[key] = None
            else:
                result[key] = max((ladder[name] for name in names if name in ladder), default=0)
        return result


def window_severity(
    item: dict[str, Any],
    corrections: dict[str, bool] | None,
    severity_map: SeverityMap,
) -> dict[str, int | None] | None:
    """Ground-truth levels for a film window (None for non-window items).

    `corrections` is the item's raw `{key: bool}` correction map. Only an explicit
    family-level `false` zeroes a family; a `true` for the family or any of its tokens
    marks it present.
    """
    if "window" not in item:
        return None
    levels = severity_map.from_tags(item.get("source_tags", []))
    present = {family_of(key) for key in item.get("expected", [])}
    for key in levels:
        if key in present and levels[key] == 0:
            levels[key] = None  # labelled present, but no tag gives it a level
    if not corrections:
        return levels
    for key in levels:
        if corrections.get(key) is False:
            levels[key] = 0
        elif any(value for name, value in corrections.items() if family_of(name) == key):
            levels[key] = levels[key] or None  # present but no tag level: unscored
    return levels


def usable_level(value: Any, max_level: int) -> int | None:
    """A predicted level as an int in 0..max_level, or None when it is unusable."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= max_level:
        return None
    return value


def quadratic_kappa(pairs: list[tuple[int, int]], levels: int) -> float | None:
    """Quadratic-weighted Cohen's kappa over `levels` ordered classes (0..levels-1).

    None when undefined: no pairs, fewer than two classes, or no expected disagreement
    (e.g. every truth and prediction in one class).
    """
    if not pairs or levels < 2:
        return None
    n = len(pairs)
    observed = [[0.0] * levels for _ in range(levels)]
    for truth, predicted in pairs:
        observed[truth][predicted] += 1
    rows = [sum(row) for row in observed]
    cols = [sum(observed[i][j] for i in range(levels)) for j in range(levels)]
    numerator = denominator = 0.0
    for i in range(levels):
        for j in range(levels):
            weight = (i - j) ** 2 / (levels - 1) ** 2
            numerator += weight * observed[i][j]
            denominator += weight * rows[i] * cols[j] / n
    return None if denominator == 0 else round(1 - numerator / denominator, 4)


def severity_metrics(
    truth: list[int | None], predicted: list[Any], max_level: int
) -> dict[str, Any]:
    """Exact and within-1 accuracy plus quadratic kappa over windows with a truth level
    and a usable predicted level. Windows with a truth level whose prediction is missing
    or out of range (an unusable reply) are counted as `unusable`, never coerced to a level.
    """
    scored = [
        (usable_level(t, max_level), usable_level(p, max_level))
        for t, p in zip(truth, predicted, strict=True)
    ]
    pairs = [(t, p) for t, p in scored if t is not None and p is not None]
    unusable = sum(1 for t, p in scored if t is not None and p is None)
    if not pairs:
        return {"n": 0, "unusable": unusable, "exact": None, "within_1": None, "kappa": None}
    return {
        "n": len(pairs),
        "unusable": unusable,
        "exact": round(sum(t == p for t, p in pairs) / len(pairs), 4),
        "within_1": round(sum(abs(t - p) <= 1 for t, p in pairs) / len(pairs), 4),
        "kappa": quadratic_kappa(pairs, max_level + 1),
    }
