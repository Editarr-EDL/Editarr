"""Pure rules for community filters held as normal saved-review filters (ADR 0002 U2–U6).

A saved review stores each included master set as a snapshot: one *entry* per filter
with the master values on the local timeline (milliseconds), and one ordinary review
filter per entry that the user may enable, disable or adjust. These helpers decide
which filters start enabled, turn an entry into review filter values, compare a filter
with its defaults, and map snapshot IDs across a refresh. Whether the *user* edited a
filter is recorded on the review filter itself (`community_edited`), so bulk changes
such as Apply Filter Defaults don't count.

Entry keys: `filter_id`, `category`, `subcategory`, `severity`, `edit_action`,
`start_ms`, `end_ms`, `low_confidence`, `confidence`, `contributor_count`,
`default_selected`, optional `initial` (`{start_ms, end_ms}` when the review filter was
clipped to the runtime), `outside` (true when no review filter was added because the
filter lay wholly outside the runtime) and `served` (false for a filter the master no
longer serves that the review keeps flagged "Removed from community"). Older snapshots lack the
newer keys; their filters started enabled.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping

from .model_core import MCF_CATEGORY_TO_FAMILY

# Master values whose change makes a stored snapshot differ from its link.
MATERIAL = ("category", "subcategory", "severity", "edit_action", "start_ms", "end_ms")


def _value(item):
    return getattr(item, "value", item)


def starts_enabled(
    *,
    low_confidence: bool,
    confidence: float | None,
    threshold: float,
    show_low_confidence: bool,
) -> bool:
    """Whether a newly included filter starts enabled: it meets the confidence threshold
    and isn't Low Confidence unless Show Low Confidence is on (U2)."""
    if low_confidence and not show_low_confidence:
        return False
    return (confidence or 0.0) >= threshold


def served(entry: Mapping) -> bool:
    return entry.get("served", True) is not False


def snapshot_digest(entries: Iterable[Mapping]) -> str:
    """Identify the served master values of a snapshot; informational fields, defaults
    and kept removed filters are left out, so only a master change counts."""
    document = [
        [entry["filter_id"], *(entry.get(name) for name in MATERIAL)]
        for entry in entries
        if served(entry)
    ]
    encoded = json.dumps(sorted(document), separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def classification(entry: Mapping) -> tuple[str | None, str | None]:
    """The entry's `(category, subcategory)`, or `(None, None)` when the subcategory
    doesn't belong to the category: the review filter then needs classification."""
    category, subcategory = entry.get("category"), entry.get("subcategory")
    if subcategory and _value(MCF_CATEGORY_TO_FAMILY.get(subcategory)) == category:
        return category, subcategory
    return None, None


def initial_bounds(entry: Mapping) -> tuple[int, int]:
    """The bounds a review filter got from this entry, after clipping to the runtime."""
    initial = entry.get("initial") or entry
    return int(initial["start_ms"]), int(initial["end_ms"])


def clip(entry: Mapping, runtime_ms: int | None) -> tuple[int, int] | None:
    """The entry's bounds within `[0, runtime]`, or None when nothing of it remains."""
    start = max(0, int(entry["start_ms"]))
    end = int(entry["end_ms"]) if runtime_ms is None else min(int(entry["end_ms"]), runtime_ms)
    return (start, end) if start < end else None


def filter_values(entry: Mapping) -> dict:
    """Review filter values for an entry's defaults: a mute is an audio mute, a skip a
    skip of both channels; labels and descriptions stay empty."""
    category, subcategory = classification(entry)
    start_ms, end_ms = initial_bounds(entry)
    mute = entry.get("edit_action") == "mute"
    return {
        "source": "audio" if mute else "visual",
        "category": category,
        "subcategory": subcategory,
        "severity": entry.get("severity") or None,
        "mcf_channel": "audio" if mute else "both",
        "mcf_comment": None,
        "label": None,
        "details": None,
        "group_key": None,
        "start_seconds": start_ms / 1000,
        "end_seconds": end_ms / 1000,
        "confidence": entry.get("confidence"),
        "edit_action": "mute" if mute else "skip",
        "selected": bool(entry.get("default_selected", True)),
    }


def values_changed(values, entry: Mapping | None, *, selection: bool = True) -> bool:
    """Whether a review filter's values differ from its entry's defaults: its bounds (to
    the millisecond), classification, severity, action and, with `selection`, its enabled
    state. `values` is any object with review filter attributes. Without an entry the
    filter can't be compared and counts as changed."""
    if entry is None:
        return True
    expected = filter_values(entry)
    start_ms, end_ms = initial_bounds(entry)
    end = getattr(values, "end_seconds", None)
    return (
        (selection and bool(values.selected) != expected["selected"])
        or round(values.start_seconds * 1000) != start_ms
        or end is None
        or round(end * 1000) != end_ms
        or any(
            _value(getattr(values, name)) != expected[name]
            for name in ("category", "subcategory", "severity", "edit_action")
        )
    )


def merge_targets(
    old_ids: Iterable[str],
    merged_ids: Mapping[str, Iterable[str]],
    served_ids: Iterable[str],
    priority: Mapping[str, int] | None = None,
) -> dict[str, str]:
    """Map old snapshot filter IDs onto the served filter that continues each one.

    An ID that is still served continues itself. Otherwise a served filter whose
    `mergedIds` lists it continues it, but only one old ID per served filter: the one
    with the lowest `priority` (unlisted IDs come last), then the lowest ID. Old IDs
    left out of the result are no longer served.
    """
    served_ids = set(served_ids)
    priority = priority or {}
    old_ids = sorted(set(old_ids))
    targets = {old: old for old in old_ids if old in served_ids}
    claimed = set(targets.values())
    for new_id in sorted(merged_ids):
        if new_id not in served_ids or new_id in claimed:
            continue
        candidates = [old for old in merged_ids[new_id] if old in old_ids and old not in targets]
        if candidates:
            chosen = min(candidates, key=lambda old: (priority.get(old, len(priority) + 1), old))
            targets[chosen] = new_id
            claimed.add(new_id)
    return targets
