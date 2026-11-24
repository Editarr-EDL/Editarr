"""Pure cue ↔ master-filter correspondence for sharing projections (D22, local API §7).

Everything is on the local file timeline: master rows are compared after the link's
alignment maps them there. Proposals are hints only; the service makes the final
match with a tolerance clients never receive, so derived counts are estimates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .community_master_state import ADJUSTED, REJECTED, VERIFIED

MAX_SCALE_DEVIATION = 0.1


@dataclass(frozen=True)
class Cue:
    """A kept local cue in milliseconds."""

    start_ms: int
    end_ms: int
    category: str


@dataclass(frozen=True)
class LinkedFilter:
    """A master filter row with its effective values on the local timeline."""

    filter_id: str
    category: str
    state: str
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
    disputes: int

    def as_json(self) -> dict:
        return {
            "approximate": True,
            "newFilters": self.new_filters,
            "confirmations": self.confirmations,
            "adjustments": self.adjustments,
            "disputes": self.disputes,
        }


def _distance(cue: Cue, row: LinkedFilter) -> tuple[int, int]:
    start, end = abs(cue.start_ms - row.start_ms), abs(cue.end_ms - row.end_ms)
    return max(start, end), start + end


def propose_filters(cues: Iterable[Cue], rows: Iterable[LinkedFilter]) -> dict[int, str]:
    """Map cue indexes to the master filter each most likely supports.

    A cue may propose a same-category, overlapping filter the user did not reject.
    Pairs are assigned closest first (largest bound difference, then summed
    difference, then cue index and filter ID), so each filter is proposed once.
    """
    cues = list(cues)
    candidates = [row for row in rows if row.state != REJECTED]
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


def shared_rows(rows: Iterable[LinkedFilter], proposed: Iterable[str]) -> tuple[list, list]:
    """Split touched rows into disputes (rejected) and supports (verified or adjusted
    rows no cue proposes). Untouched rows are never shared."""
    proposed = set(proposed)
    disputes, supports = [], []
    for row in rows:
        if row.state == REJECTED:
            disputes.append(row)
        elif row.state in (VERIFIED, ADJUSTED) and row.filter_id not in proposed:
            supports.append(row)
    return disputes, supports


def estimate(
    proposals: Mapping[int, str],
    cue_count: int,
    row_supports: Iterable[LinkedFilter],
    disputes: Iterable[LinkedFilter],
) -> Estimates:
    """≈ new filters (unproposed cues), confirmations (supports of an existing filter),
    adjustments (shared adjusted rows) and disputes."""
    row_supports = list(row_supports)
    return Estimates(
        new_filters=cue_count - len(proposals),
        confirmations=len(proposals) + len(row_supports),
        adjustments=sum(1 for row in row_supports if row.state == ADJUSTED),
        disputes=len(list(disputes)),
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
