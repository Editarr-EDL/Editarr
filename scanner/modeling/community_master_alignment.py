"""Fit the master→local transform of a fuzzy master set from user anchors (local API §4).

The transform is `local = master × scale + offsetMs`. At least three anchors spanning
300 s or more fit an offset and a drift (median of pairwise slopes, then the median
residual offset); anything less fits only the median offset.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from .community_filter_proposals import MAX_SCALE_DEVIATION

DRIFT_MIN_ANCHORS = 3
DRIFT_MIN_SPAN_MS = 300_000
MAX_ANCHORS = 50


class AlignmentFitError(ValueError):
    """The anchors cannot give a usable transform; `code` is the local API error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AnchorPair:
    """A master time and the local time the user placed it at, in milliseconds."""

    master_ms: int
    local_ms: int


def fit_alignment(anchors: Sequence[AnchorPair]) -> dict:
    """Return `{offsetMs, scale, anchors}` for 1–50 anchors.

    A drift outside ±10% is `alignment_out_of_range`, matching the inverse transform
    the sharing projection accepts.
    """
    if not 1 <= len(anchors) <= MAX_ANCHORS:
        raise AlignmentFitError("invalid_alignment")
    masters = [anchor.master_ms for anchor in anchors]
    scale = 1.0
    if len(anchors) >= DRIFT_MIN_ANCHORS and max(masters) - min(masters) >= DRIFT_MIN_SPAN_MS:
        slopes = [
            (right.local_ms - left.local_ms) / (right.master_ms - left.master_ms)
            for index, left in enumerate(anchors)
            for right in anchors[index + 1 :]
            if right.master_ms != left.master_ms
        ]
        scale = round(median(slopes), 9)
        if abs(scale - 1.0) > MAX_SCALE_DEVIATION:
            raise AlignmentFitError("alignment_out_of_range")
    offset = int(round(median(anchor.local_ms - anchor.master_ms * scale for anchor in anchors)))
    return {"offsetMs": offset, "scale": scale, "anchors": len(anchors)}


def require_in_bounds(
    ranges: Sequence[tuple[int, int]], alignment: dict, duration_ms: int | None
) -> None:
    """Refuse a transform that puts any master range wholly before the local file start
    or, when the duration is known, wholly after its end (`alignment_out_of_range`).

    A range that only partly leaves the file is kept and clipped where it is used.
    """
    scale, offset = alignment["scale"], alignment["offsetMs"]
    for start, end in ranges:
        local_start = int(round(start * scale)) + offset
        local_end = int(round(end * scale)) + offset
        if local_end <= 0 or (duration_ms is not None and local_start >= duration_ms):
            raise AlignmentFitError("alignment_out_of_range")


def offset_alignment(offset_ms: int) -> dict:
    """An offset-only transform entered directly."""
    return {"offsetMs": int(offset_ms), "scale": 1.0, "anchors": 0}
