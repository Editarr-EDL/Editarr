"""Pure local state rules for downloaded master filters (local API §3 and §5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .community_master_diff import VALUE_FIELDS, MasterValues

PENDING = "pending"
VERIFIED = "verified"
ADJUSTED = "adjusted"
REJECTED = "rejected"
LOW_CONFIDENCE_STATE = "low_confidence"
STATES = (PENDING, VERIFIED, ADJUSTED, REJECTED, LOW_CONFIDENCE_STATE)
_IN_EFFECT = (PENDING, VERIFIED, ADJUSTED)
# Local column names for each value field.
LOCAL_COLUMNS = {
    "startMs": "local_start_ms",
    "endMs": "local_end_ms",
    "subcategory": "local_subcategory",
    "severity": "local_severity",
    "action": "local_action",
}


@dataclass(frozen=True)
class Preferences:
    """An account's display preferences; the defaults are 0.0 and False."""

    confidence_threshold: float = 0.0
    show_low_confidence: bool = False


def untouched_state(values: MasterValues) -> str:
    """The state of a row the user has not touched."""
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


def effective(master: Mapping, local: Mapping) -> dict:
    """Each value field from the local override when one is set, or else the master."""
    return {
        name: local[name] if local.get(name) is not None else master[name] for name in VALUE_FIELDS
    }


def overrides(effective_values: Mapping, master: Mapping) -> dict:
    """The local overrides that keep `effective_values` over `master`; null when equal."""
    return {
        name: None if effective_values[name] == master[name] else effective_values[name]
        for name in VALUE_FIELDS
    }


def is_active(
    *,
    state: str,
    touched: bool,
    present: bool,
    low_confidence: bool,
    confidence: float | None,
    alignment_state: str,
    preferences: Preferences,
) -> bool:
    """Whether a row is in effect.

    The link must not await alignment, and the row must be in the local set. A touched
    row is active when pending, verified or adjusted, ignoring the threshold. An
    untouched row also needs a confidence at or above the threshold and must not be
    Low Confidence unless `show_low_confidence` is set, which also admits the
    `low_confidence` state.
    """
    if alignment_state == "required" or not present:
        return False
    if touched:
        return state in _IN_EFFECT
    if low_confidence:
        if not preferences.show_low_confidence or state not in (*_IN_EFFECT, LOW_CONFIDENCE_STATE):
            return False
    elif state not in _IN_EFFECT:
        return False
    return (confidence or 0.0) >= preferences.confidence_threshold


def intervals_overlap(start_ms: int, end_ms: int, start_s: float, end_s: float | None) -> bool:
    """Whether a filter in milliseconds overlaps a finding in seconds (a point if no end)."""
    finding_start = start_s * 1000
    finding_end = (end_s if end_s is not None else start_s) * 1000
    return start_ms <= finding_end and finding_start <= end_ms
