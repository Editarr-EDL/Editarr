"""Track deliberate changes to saved filter content independently of review policy."""

from math import isclose

from scanner.db import ScanFindingRow
from scanner.mcf import TimingInterval, map_affine_range
from scanner.models import ReviewFilterInput, SavedReviewFilter
from scanner.settings.filter_defaults import fill_filter_defaults

from .community_import_targets import project_review_channel, verified_target
from .filter_preferences import base_filter_settings
from .review_policy import _map_policy_floor, _source_filter, prepare_review_policy

_CONTENT_FIELDS = (
    "category",
    "subcategory",
    "severity",
    "mcf_channel",
    "mcf_comment",
    "edit_action",
    "label",
    "details",
    "group_key",
)


def source_cue_flags(scan, finding) -> tuple[bool | None, bool | None]:
    """Reuse explicit source evidence. Generated findings have an unedited baseline."""
    if finding is None:
        return None, None
    if scan.origin == "generated":
        return False, False
    return finding.manually_added, finding.manually_edited


def cue_provenance(row) -> tuple[str, int | None]:
    """Identify the immutable source independently of a draft's row ID."""
    return row.source_scan_id, row.source_finding_id


def _source_baselines(db, review, rows, memberships) -> dict:
    """Normalize incoming source copies the same way as creating a review."""
    inputs = []
    for row in rows:
        if row.manually_added is True or row.source_finding_id is None:
            continue
        finding = db.get(ScanFindingRow, row.source_finding_id)
        if finding is None:
            continue
        membership = memberships[row.source_scan_id]
        target = verified_target(db, membership.scan, review.media_item_id, review.media_source_id)
        source = _source_filter(finding, row.id).model_dump()
        source["mcf_channel"] = project_review_channel(target, finding.mcf_channel, membership.role)
        inputs.append(ReviewFilterInput.model_validate(source))
    if not inputs:
        return {}
    configured = fill_filter_defaults(inputs, base_filter_settings(db, review.owner_id))
    return {row.id: row for row in configured.filters}


def _timing(row) -> tuple[float, float | None]:
    return row.start_seconds, row.end_seconds


def _same_retime(actual, expected) -> bool:
    return all(
        left == right
        or (
            left is not None and right is not None and isclose(left, right, abs_tol=1e-7, rel_tol=0)
        )
        for left, right in zip(actual, expected, strict=True)
    )


def track_manual_edits(db, review, replacement, synchronization, memberships) -> None:
    """Count surviving hand changes once, leaving selection, defaults and retiming out."""
    from .repository_review_retime import _has_unknown_cue_provenance

    previous = {row.id: row for row in review.filters}
    by_provenance = {
        cue_provenance(row): row for row in review.filters if row.source_finding_id is not None
    }
    baselines = {}
    for row in replacement:
        before = previous.get(row.id) or by_provenance.get(cue_provenance(row))
        if before is not None:
            baselines[row.id] = before
    baselines.update(
        _source_baselines(
            db, review, (row for row in replacement if row.id not in baselines), memberships
        )
    )
    if review.synchronization is None:
        timing_change = False
    else:
        source = TimingInterval(*_timing(review.synchronization))
        destination = TimingInterval(*_timing(synchronization))
        timing_change = source != destination
    policy_view = None
    for row in replacement:
        before = baselines.get(row.id)
        if before is None or row.manually_edited is True:
            continue
        if any(getattr(before, key) != getattr(row, key) for key in _CONTENT_FIELDS):
            row.manually_edited = True
            continue
        if _timing(before) == _timing(row):
            continue
        if policy_view is None:
            prepared = prepare_review_policy(db, review)
            policy_view = {value.id: value for value in prepared.filters}
            required = prepared.required_ids
        effective = policy_view.get(before.id)
        if effective is not None and _timing(row) == _timing(effective):
            continue
        if timing_change and _has_unknown_cue_provenance(memberships[row.source_scan_id]):
            mapper = _map_policy_floor if before.id in required else map_affine_range
            try:
                expected = mapper(*_timing(effective or before), source, destination)
            except ValueError:
                pass
            else:
                if _same_retime(_timing(row), expected):
                    continue
        row.manually_edited = True


def label_edited(row) -> bool:
    """Whether a stored filter's label differs from its source finding's label."""
    finding = row.source_finding
    if finding is None:
        return False
    return (row.label or None) != (finding.label or None)


def with_label_provenance(review, filters) -> list[SavedReviewFilter]:
    """Attach each effective filter's label provenance from its stored row."""
    stored = {value.id: value for value in review.filters}
    return [
        SavedReviewFilter(
            **value.model_dump(),
            label_edited=value.id in stored and label_edited(stored[value.id]),
        )
        for value in filters
    ]
