"""Community filters as normal saved-review filters (local API §3, ADR 0002 U2–U4).

Including a master set stores a snapshot of its served filters (review_community_sources)
and one ordinary `review_filters` row per snapshot entry, which then takes part in
selection, adjustment, policy, exports, sidecars and subtitles like a scan filter; only
its provenance (`community_source_id`, `community_filter_id`) differs. Below-threshold
and Low Confidence filters start disabled. A refresh takes a new snapshot and keeps the
user's edits by filter ID (following merges); unedited filters take the new master
values, new ones start enabled or disabled the same way, and filters the master no
longer serves stay flagged "Removed from community", disabled unless the user edited them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    ReviewCommunitySourceRow,
    ReviewFilterRow,
    SavedReviewRow,
)
from scanner.modeling.community_master_diff import master_filters
from scanner.modeling.community_review_filters import (
    clip,
    filter_values,
    is_edited,
    merge_targets,
    served,
    starts_enabled,
)
from scanner.models import ReviewFilterCommunity

from .community_master_filters import get_preferences, master_values
from .community_master_links import alignment_of, latest_snapshot


def _membership_error(message: str) -> ValueError:
    from scanner.repository import ReviewMembershipError

    return ReviewMembershipError(message)


@dataclass
class SourcePlan:
    """One included snapshot in a save: `new` (just captured from a link), `refresh`
    (re-captured; `previous` holds the entries it replaced) or `keep`."""

    row: ReviewCommunitySourceRow
    mode: str
    previous: list[dict] | None = None


def entries_of(row: ReviewCommunitySourceRow) -> list[dict]:
    return json.loads(row.filters_json)


def _store_entries(row: ReviewCommunitySourceRow, entries: list[dict]) -> None:
    row.filters_json = json.dumps(entries, sort_keys=True, separators=(",", ":"))


def link_entries(db: Session, link: CommunityMasterLinkRow) -> list[dict]:
    """The link's served filters as snapshot entries, in master start order, with the
    owner's threshold and Low Confidence preference deciding `default_selected`."""
    preferences = get_preferences(db, link.owner_id)
    alignment = alignment_of(link)
    entries = []
    rows = db.scalars(
        select(CommunityMasterFilterRow).where(
            CommunityMasterFilterRow.link_id == link.id, CommunityMasterFilterRow.present
        )
    )
    for row in rows:
        values = master_values(row, alignment)
        info = json.loads(row.info_json)
        entries.append(
            {
                "filter_id": row.filter_id,
                "category": row.category,
                "subcategory": values["subcategory"],
                "severity": values["severity"],
                "edit_action": values["action"],
                "start_ms": values["startMs"],
                "end_ms": values["endMs"],
                "low_confidence": bool(values.get("lowConfidence")),
                "confidence": info.get("confidence"),
                "contributor_count": info.get("contributorCount"),
                "default_selected": starts_enabled(
                    low_confidence=bool(values.get("lowConfidence")),
                    confidence=info.get("confidence"),
                    threshold=preferences.confidence_threshold,
                    show_low_confidence=preferences.show_low_confidence,
                ),
            }
        )
    entries.sort(key=lambda entry: (entry["start_ms"], entry["end_ms"], entry["filter_id"]))
    return entries


def link_merges(db: Session, link: CommunityMasterLinkRow | None) -> dict[str, tuple[str, ...]]:
    """`mergedIds` of each served filter in the link's latest verified snapshot."""
    snapshot = latest_snapshot(db, link) if link is not None else None
    if snapshot is None:
        return {}
    try:
        filters = master_filters(json.loads(snapshot.snapshot_bytes))
    except (ValueError, KeyError, TypeError):
        return {}
    return {item.filter_id: item.merged_ids for item in filters if item.merged_ids}


def _row(review_id: str, source_id: str, entry: Mapping, values: Mapping | None = None):
    values = dict(values or filter_values(entry))
    return ReviewFilterRow(
        review_id=review_id,
        id=values.pop("id", None) or str(uuid.uuid4()),
        source_scan_id=None,
        source_finding_id=None,
        community_source_id=source_id,
        community_filter_id=entry["filter_id"],
        **values,
    )


_VALUE_FIELDS = (
    "source",
    "category",
    "subcategory",
    "severity",
    "mcf_channel",
    "mcf_comment",
    "label",
    "details",
    "group_key",
    "start_seconds",
    "end_seconds",
    "confidence",
    "edit_action",
    "selected",
)


def _values(value) -> dict:
    """Stored field values of an input or row, enums as their values."""
    return {
        "id": value.id,
        **{
            name: getattr(getattr(value, name), "value", getattr(value, name))
            for name in _VALUE_FIELDS
        },
    }


def _with_initial(entries: list[dict], runtime_ms: int | None) -> list[dict]:
    """Record the runtime-clipped bounds a new review filter gets; a filter wholly
    outside the file gets none and is marked `outside`."""
    result = []
    for entry in entries:
        entry = {key: value for key, value in entry.items() if key not in ("initial", "outside")}
        bounds = clip(entry, runtime_ms)
        if bounds is None:
            entry["outside"] = True
        elif bounds != (entry["start_ms"], entry["end_ms"]):
            entry["initial"] = {"start_ms": bounds[0], "end_ms": bounds[1]}
        result.append(entry)
    return result


def _default_rows(review_id: str, row: ReviewCommunitySourceRow, runtime_ms: int | None):
    entries = _with_initial(entries_of(row), runtime_ms)
    _store_entries(row, entries)
    return [_row(review_id, row.id, entry) for entry in entries if not entry.get("outside")]


def _check_provenance(current: list, entries: Mapping, previous: Mapping) -> None:
    """Each submitted filter must name one snapshot entry, with unchanged provenance."""
    for value in current:
        prior = previous.get(value.id)
        if value.community_filter_id not in entries or (
            prior is not None
            and (prior.community_source_id, prior.community_filter_id, prior.source)
            != (value.community_source_id, value.community_filter_id, value.source)
        ):
            raise _membership_error("Review filter provenance is invalid")


def _refreshed_rows(
    db: Session,
    review_id: str,
    plan: SourcePlan,
    current: list,
    previous: Mapping,
    runtime_ms: int | None,
):
    """Merge the user's current filters of a refreshed source into its new snapshot."""
    old = {entry["filter_id"]: entry for entry in plan.previous or ()}
    _check_provenance(current, old, previous)
    new_entries = _with_initial(entries_of(plan.row), runtime_ms)
    new = {entry["filter_id"]: entry for entry in new_entries}
    by_filter = {value.community_filter_id: value for value in current}
    edited = {fid for fid, value in by_filter.items() if is_edited(value, old.get(fid))}
    # A merged filter continues an edited review filter first, then any other one,
    # and a filter the user deleted from the review last.
    targets = merge_targets(
        list(old) + list(by_filter),
        link_merges(db, plan.row.link),
        list(new),
        {fid: 0 if fid in edited else 1 for fid in by_filter},
    )
    rows, kept = [], []
    claimed = set(targets.values())
    for fid, value in by_filter.items():
        target = targets.get(fid)
        if target is not None:
            entry = new[target]
            if fid in edited:
                rows.append(_row(review_id, plan.row.id, entry, _values(value)))
            elif not entry.get("outside"):
                values = {"id": value.id, **filter_values(entry)}
                rows.append(_row(review_id, plan.row.id, entry, values))
            continue
        gone = {**old[fid], "served": False}
        values = _values(value)
        if fid not in edited:
            # Disabled for the user, so it still counts as untouched.
            values["selected"] = gone["default_selected"] = False
        kept.append(gone)
        rows.append(_row(review_id, plan.row.id, gone, values))
    # Filters the user deleted from the review stay deleted, whatever they became.
    deleted = {targets[fid] for fid in old if fid not in by_filter and fid in targets}
    for entry in new_entries:
        fid = entry["filter_id"]
        if fid in claimed or fid in deleted or entry.get("outside"):
            continue
        rows.append(_row(review_id, plan.row.id, entry))
    _store_entries(plan.row, new_entries + kept)
    return rows


def _kept_rows(review_id: str, plan: SourcePlan, current: list, previous: Mapping):
    """A kept source's submitted filters, unchanged."""
    entries = {entry["filter_id"]: entry for entry in entries_of(plan.row)}
    _check_provenance(current, entries, previous)
    return [
        _row(review_id, plan.row.id, entries[value.community_filter_id], _values(value))
        for value in current
    ]


def community_rows(
    db: Session,
    review: SavedReviewRow,
    plans: list[SourcePlan],
    inputs: Iterable,
    runtime_seconds: float | None,
) -> list[ReviewFilterRow]:
    """The review's community filter rows after a save, in source order.

    `inputs` are the submitted community filters (with `community_source_id`); those of
    removed sources are dropped. New sources get default rows, refreshed ones merge the
    submitted filters into the new snapshot, and kept ones keep the submitted values.
    """
    runtime_ms = round(runtime_seconds * 1000) if runtime_seconds else None
    by_source: dict[str, list] = {}
    for value in inputs:
        by_source.setdefault(value.community_source_id, []).append(value)
    filter_ids = [(value.community_source_id, value.community_filter_id) for value in inputs]
    if len(set(filter_ids)) != len(filter_ids):
        raise _membership_error("Review filter source provenance must be unique")
    known = {plan.row.id for plan in plans}
    removed = {row.id for row in review.community_sources} - known
    if set(by_source) - known - removed:
        raise _membership_error("Review filters must reference an included community source")
    previous = {row.id: row for row in review.filters if row.community_source_id is not None}
    rows: list[ReviewFilterRow] = []
    for plan in plans:
        current = by_source.get(plan.row.id, [])
        if plan.mode == "new":
            rows.extend(_default_rows(review.id, plan.row, runtime_ms))
        elif plan.mode == "refresh":
            rows.extend(_refreshed_rows(db, review.id, plan, current, previous, runtime_ms))
        else:
            rows.extend(_kept_rows(review.id, plan, current, previous))
    return rows


def annotations(db: Session, review: SavedReviewRow) -> dict[str, ReviewFilterCommunity]:
    """Read-only community provenance for each community filter of the review, by ID."""
    sources = {
        row.id: (row, {e["filter_id"]: e for e in entries_of(row)})
        for row in review.community_sources
    }
    verified = _locally_verified(db, review.community_sources)
    found = {}
    for row in review.filters:
        if row.community_source_id is None or row.community_source_id not in sources:
            continue
        source, entries = sources[row.community_source_id]
        entry = entries.get(row.community_filter_id)
        found[row.id] = ReviewFilterCommunity(
            source_id=source.id,
            filter_id=row.community_filter_id,
            label=source.label,
            master_revision=source.master_revision,
            confidence=(entry or {}).get("confidence"),
            contributor_count=(entry or {}).get("contributor_count"),
            low_confidence=bool((entry or {}).get("low_confidence")),
            removed=entry is not None and not served(entry),
            edited=is_edited(row, entry),
            locally_verified=(source.link_id, row.community_filter_id) in verified,
        )
    return found


def _locally_verified(db: Session, sources) -> set[tuple[str, str]]:
    """`(link_id, filter_id)` pairs the review's links mark Locally verified (U7)."""
    link_ids = [row.link_id for row in sources if row.link_id is not None]
    if not link_ids:
        return set()
    return set(
        db.execute(
            select(CommunityMasterFilterRow.link_id, CommunityMasterFilterRow.filter_id).where(
                CommunityMasterFilterRow.link_id.in_(link_ids),
                CommunityMasterFilterRow.locally_verified,
            )
        ).all()
    )


def review_filter_rows(
    db: Session,
    review: SavedReviewRow,
    memberships: Mapping,
    inputs: Iterable,
    plans: list[SourcePlan],
) -> list[ReviewFilterRow]:
    """Build every stored filter of a save: scan filters from their inputs, then each
    included source's community filters (see `community_rows`), positioned in order.

    `memberships` maps scan IDs to the saved membership rows; community filters are
    clipped to the runtime of those scans (or of the review's local versions).
    """
    from scanner import repository as root

    from .review_community_sources import export_runtime

    inputs = list(inputs)
    scan_inputs = [value for value in inputs if value.community_source_id is None]
    rows = root._filter_rows_from_inputs(
        db, review_id=review.id, memberships=dict(memberships), filters=scan_inputs
    )
    runtime = export_runtime(db, review, [value.scan for value in memberships.values()])
    rows += community_rows(
        db,
        review,
        plans,
        [value for value in inputs if value.community_source_id is not None],
        runtime,
    )
    if len({row.id for row in rows}) != len(rows):
        raise _membership_error("Review filter IDs must be unique")
    for position, row in enumerate(rows):
        row.position = position
    return rows
