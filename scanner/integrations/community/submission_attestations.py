"""Community review filters as submission attestations (ADR 0002 U6, local API §7).

A saved review's community filters are normal review filters; sharing the review turns
only the ones the user edited directly (`community_edited`; bulk defaults don't count)
into attestations of the master filter they came from:

| Review filter the user edited | Entry |
| --- | --- |
| Enabled | `support` with the user's bounds, subcategory, severity and action |
| Disabled, though it started enabled | `dispute` of the master filter |
| Disabled, and it started disabled | not shared, even with adjusted values |

Untouched filters, filters of a snapshot of another community version than the
submission's link, and filters the master no longer serves are not shared. All times
are milliseconds on the local file.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

from scanner.modeling.community_review_filters import (
    classification,
    initial_bounds,
    served,
    values_changed,
)


@dataclass(frozen=True)
class Attestations:
    entries: tuple[dict, ...]
    filter_ids: frozenset[str]
    supports: int
    adjustments: int
    disputes: int


def _value(item):
    return getattr(item, "value", item)


def _evidence(verified: bool) -> dict:
    return {
        "origin": "downloaded",
        "manualReview": True,
        "locallyVerified": verified,
        "modelVersion": None,
    }


def _entries(data: dict, version_id: str | None) -> dict:
    """Snapshot entries by `(source_id, filter_id)`, of snapshots of `version_id` only."""
    return {
        (source["id"], entry["filter_id"]): entry
        for source in data.get("community_sources", ())
        if version_id is not None and source["community_version_id"] == version_id
        for entry in json.loads(source["filters_json"])
    }


def review_attestations(
    data: dict,
    duration_ms: int,
    verified: Callable[[str, int, int], bool],
    warnings: list[str],
    version_id: str | None,
) -> Attestations:
    """Supports and disputes for the edited community filters of a captured review.

    Only snapshots of `version_id`, the community version of the submission's link, are
    attested. `verified(category, start_ms, end_ms)` tells whether a local model finding
    of the category overlaps the bounds (Locally verified, U7).
    """
    snapshot = _entries(data, version_id)
    entries, ids = [], set()
    supports = adjustments = disputes = 0
    for row in data.get("base_findings", ()):
        source_id = row.get("community_source_id")
        if not source_id:
            continue
        entry = snapshot.get((source_id, row["community_filter_id"]))
        if entry is None or not served(entry) or not row.get("community_edited"):
            continue
        filter_id = row["community_filter_id"]
        if not row["selected"]:
            if not entry.get("default_selected", True):
                continue
            start, end = initial_bounds(entry)
            category = classification(entry)[0] or entry.get("category")
            item = _entry("dispute", filter_id, start, end, duration_ms, category)
            if item is None:
                warnings.append("master_row_outside_media")
                continue
            entries.append(item)
            ids.add(filter_id)
            disputes += 1
            continue
        category, subcategory = _value(row.get("category")), row.get("subcategory")
        severity, end_seconds = _value(row.get("severity")), row.get("end_seconds")
        if not (category and subcategory and severity) or end_seconds is None:
            warnings.append("unclassified_cue_skipped")
            continue
        start = round(row["start_seconds"] * 1000)
        end = round(end_seconds * 1000)
        item = _entry("support", filter_id, start, end, duration_ms, category)
        if item is None:
            warnings.append("cue_outside_media")
            continue
        item.update(
            subcategory=subcategory,
            severity=severity,
            action="mute" if _value(row.get("edit_action")) == "mute" else "skip",
            evidence=_evidence(verified(category, item["startMs"], item["endMs"])),
        )
        entries.append(item)
        ids.add(filter_id)
        supports += 1
        adjustments += values_changed(SimpleNamespace(**row), entry, selection=False)
    return Attestations(tuple(entries), frozenset(ids), supports, adjustments, disputes)


def _entry(kind, filter_id, start, end, duration_ms, category) -> dict | None:
    start, end = max(0, min(duration_ms, start)), max(0, min(duration_ms, end))
    if not start < end:
        return None
    return {
        "kind": kind,
        "proposedFilterId": filter_id,
        "startMs": start,
        "endMs": end,
        "category": category,
        "subcategory": None,
        "severity": None,
        "action": None,
        "evidence": _evidence(False),
    }
