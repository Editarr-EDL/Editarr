"""Pure local rules for downloaded master filters (local API §3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .community_master_diff import MasterValues

# Stored row states. Local decisions are retired (ADR 0002 U1): new rows are `pending`
# or `low_confidence`, and older decision states stay in the database unused.
PENDING = "pending"
LOW_CONFIDENCE_STATE = "low_confidence"


@dataclass(frozen=True)
class Preferences:
    """An account's master-set preferences; the defaults are 0.0, False and False.

    The threshold and Show Low Confidence decide which community filters start
    enabled when a review includes or refreshes them. `hide_rejected_downvoted` only
    hides downvoted rows from the master list.
    """

    confidence_threshold: float = 0.0
    show_low_confidence: bool = False
    hide_rejected_downvoted: bool = False


def untouched_state(values: MasterValues) -> str:
    """The stored state of a row: `low_confidence` for a Low Confidence master."""
    return LOW_CONFIDENCE_STATE if values.low_confidence else PENDING


def aligned(values: Mapping, alignment: Mapping | None) -> dict:
    """Map master bounds to the local timeline: `local = master × scale + offset`."""
    result = dict(values)
    if alignment:
        scale = float(alignment.get("scale", 1.0))
        offset = int(alignment.get("offsetMs", 0))
        for name in ("startMs", "endMs"):
            result[name] = int(round(values[name] * scale)) + offset
    return result


def intervals_overlap(start_ms: int, end_ms: int, start_s: float, end_s: float | None) -> bool:
    """Whether a filter in milliseconds overlaps a finding in seconds (a point if no end)."""
    finding_start = start_s * 1000
    finding_end = (end_s if end_s is not None else start_s) * 1000
    return start_ms <= finding_end and finding_start <= end_ms
