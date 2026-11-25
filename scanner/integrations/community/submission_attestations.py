"""Community review filters as submission attestations (ADR 0002 U6, local API §7).

A saved review's community filters are normal review filters; sharing the review turns
only the enabled ones the user edited directly (`community_edited`: times,
classification, severity, action or label; turning a filter on or off and bulk
defaults don't count) into `support` attestations of the master filter they came from,
with the user's bounds, subcategory, severity, action and, for a language filter, its
label as the expression. Everything else is not shared: disabled filters (turning a
filter off is a personal choice, never a dispute), untouched filters, filters of a
snapshot of another community version than the submission's link, and filters the
master no longer serves. All times are milliseconds on the local file.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

from scanner.formats.community.artifact_v3_models import normalize_expression
from scanner.modeling.community_review_filters import served, values_changed


@dataclass(frozen=True)
class Attestations:
    entries: tuple[dict, ...]
    filter_ids: frozenset[str]
    supports: int
    adjustments: int


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
    """Supports for the enabled, edited community filters of a captured review.

    Only snapshots of `version_id`, the community version of the submission's link, are
    attested. `verified(category, start_ms, end_ms)` tells whether a local model finding
    of the category overlaps the bounds (Locally verified, U7).
    """
    snapshot = _entries(data, version_id)
    entries, ids = [], set()
    supports = adjustments = 0
    for row in data.get("base_findings", ()):
        source_id = row.get("community_source_id")
        if not source_id or not row["selected"] or not row.get("community_edited"):
            continue
        entry = snapshot.get((source_id, row["community_filter_id"]))
        if entry is None or not served(entry):
            continue
        category, subcategory = _value(row.get("category")), row.get("subcategory")
        severity, end_seconds = _value(row.get("severity")), row.get("end_seconds")
        if not (category and subcategory and severity) or end_seconds is None:
            warnings.append("unclassified_cue_skipped")
            continue
        start = max(0, min(duration_ms, round(row["start_seconds"] * 1000)))
        end = max(0, min(duration_ms, round(end_seconds * 1000)))
        if not start < end:
            warnings.append("cue_outside_media")
            continue
        entries.append(
            {
                "kind": "support",
                "proposedFilterId": row["community_filter_id"],
                "startMs": start,
                "endMs": end,
                "category": category,
                "subcategory": subcategory,
                "severity": severity,
                "action": "mute" if _value(row.get("edit_action")) == "mute" else "skip",
                "expression": cue_expression(category, row.get("label")),
                "evidence": _evidence(verified(category, start, end)),
            }
        )
        ids.add(row["community_filter_id"])
        supports += 1
        adjustments += values_changed(SimpleNamespace(**row), entry)
    return Attestations(tuple(entries), frozenset(ids), supports, adjustments)


def cue_expression(category, label) -> str | None:
    """The shared expression of a cue: its normalized label for a language cue, else None."""
    return normalize_expression(label) if _value(category) == "language" else None
