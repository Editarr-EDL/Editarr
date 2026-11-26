"""Pure cue ↔ master-filter correspondence for sharing projections (D22, local API §7).

Everything is on the local file timeline: master rows are compared after the link's
alignment maps them there. Proposals are hints only; the service makes the final
match with a tolerance clients never receive, so derived counts are estimates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

MAX_SCALE_DEVIATION = 0.1


@dataclass(frozen=True)
class Cue:
    """A kept local cue in milliseconds."""

    start_ms: int
    end_ms: int
    category: str


@dataclass(frozen=True)
class LinkedFilter:
    """A served master filter with its values on the local timeline."""

    filter_id: str
    category: str
    start_ms: int
    end_ms: int
    subcategory: str
    severity: str
    action: str


@dataclass(frozen=True)
class Estimates:
    new_filters: int
    confirmations: int
    adjustments: int

    def as_json(self) -> dict:
        return {
            "approximate": True,
            "newFilters": self.new_filters,
            "confirmations": self.confirmations,
            "adjustments": self.adjustments,
        }


def _distance(cue: Cue, row: LinkedFilter) -> tuple[int, int]:
    start, end = abs(cue.start_ms - row.start_ms), abs(cue.end_ms - row.end_ms)
    return max(start, end), start + end


def propose_filters(
    cues: Iterable[Cue], rows: Iterable[LinkedFilter], excluded: Iterable[str] = ()
) -> dict[int, str]:
    """Map cue indexes to the master filter each most likely supports.

    A cue may propose a same-category, overlapping filter that isn't `excluded` (the
    filters a review already attests to). Pairs are assigned closest first (largest
    bound difference, then summed difference, then cue index and filter ID), so each
    filter is proposed once.
    """
    cues = list(cues)
    excluded = set(excluded)
    candidates = [row for row in rows if row.filter_id not in excluded]
    pairs = sorted(
        (*_distance(cue, row), index, row.filter_id)
        for index, cue in enumerate(cues)
        for row in candidates
        if row.category == cue.category and cue.start_ms < row.end_ms and row.start_ms < cue.end_ms
    )
    chosen: dict[int, str] = {}
    claimed: set[str] = set()
    for _largest, _total, index, filter_id in pairs:
        if index not in chosen and filter_id not in claimed:
            chosen[index] = filter_id
            claimed.add(filter_id)
    return chosen


def estimate(
    proposals: Mapping[int, str],
    cue_count: int,
    supports: int = 0,
    adjustments: int = 0,
) -> Estimates:
    """≈ new filters (unproposed cues), confirmations (cues proposing an existing filter,
    and supported community filters) and adjustments (supported community filters the
    user changed)."""
    return Estimates(
        new_filters=cue_count - len(proposals),
        confirmations=len(proposals) + supports,
        adjustments=adjustments,
    )


def inverse_alignment(alignment: Mapping | None) -> tuple[int, float] | None:
    """The local→master transform `(offsetMs, scale)` inverting a link's master→local
    `local = master × scale + offset`; None when the inverse is outside 0.9–1.1."""
    if not alignment:
        return 0, 1.0
    scale = float(alignment.get("scale", 1.0))
    offset = int(alignment.get("offsetMs", 0))
    if scale <= 0:
        return None
    inverse = 1.0 / scale
    if abs(inverse - 1.0) > MAX_SCALE_DEVIATION:
        return None
    return int(round(-offset * inverse)), inverse
