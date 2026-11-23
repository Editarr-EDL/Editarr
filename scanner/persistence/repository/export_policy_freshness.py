"""Detect newly required edits missing from a retained canonical export."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace

from scanner.formats.edl import parse_edl_content
from scanner.mcf import parse_mcf_content
from scanner.modeling.model_core import channel_for_source
from scanner.models import (
    EditAction,
    ExportFormat,
    FilterRuleDisposition,
    McfCategory,
    McfChannel,
    ReviewFilter,
    ReviewSynchronization,
    ScanFinding,
    Severity,
)
from scanner.settings.filter_defaults import inferred_channel_policy_floor

from .export_publication import POLICY_CHANNEL_EVALUATION, SNAPSHOT_VERSION
from .review_policy_evidence import CHANNEL_COVERAGE, evidence_interval, policy_export_interval


@dataclass(frozen=True)
class _RequiredInterval:
    start: float | None
    end: float | None
    edl_action: EditAction
    mcf_key: tuple[McfCategory | None, Severity | None, McfChannel]


def missing_channel_policy_coverage(snapshot, export_format, content, policy):
    """Check unmarked snapshots for uncovered channel-specific Deny rules without disk reads."""
    if (
        snapshot.get("version") != SNAPSHOT_VERSION
        or snapshot.get("policy_channel_evaluation") == POLICY_CHANNEL_EVALUATION
        or not any(
            rule.disposition is FilterRuleDisposition.DENY
            and rule.mcf_channel in {"audio", "video"}
            for rule in policy.rules
        )
    ):
        return False

    try:
        floors = _new_policy_floors(snapshot, policy)
    except (AttributeError, KeyError, TypeError, ValueError):
        # Old or incomplete snapshots cannot establish a newly missing cue.
        return False
    if not floors:
        return False
    try:
        coverage = _published_coverage(export_format, content)
        return any(not _covered(coverage, export_format, floor) for floor in floors)
    except (ArithmeticError, TypeError, ValueError):
        # There is a newly mandatory cue but its published coverage cannot be verified.
        return True


def _new_policy_floors(snapshot, policy):
    review_data = snapshot["review"]
    base_data = review_data.get("base_filters", ())
    saved = {
        value["source_finding_id"]: value for value in base_data if value.get("source_finding_id")
    }
    bindings = {value["source_finding_id"]: value for value in snapshot["policy_evidence"]}
    relevant_ids = saved.keys() | bindings.keys()
    # Retained sources include findings outside the selected scan role. A base
    # filter or immutable timing binding establishes relevance to the publication.
    new_sources = [
        floor
        for source in snapshot["sources"]
        for finding in source["findings"]
        if finding["id"] in relevant_ids and finding.get("mcf_channel") is None
        if (floor := inferred_channel_policy_floor(ScanFinding.model_validate(finding), policy))
        is not None
    ]
    floors = [
        _required_interval(floor, floor.start_seconds, floor.end_seconds, floor)
        for value in base_data
        if value.get("mcf_channel") is None
        if (floor := inferred_channel_policy_floor(ReviewFilter.model_validate(value), policy))
        is not None
    ]
    if not new_sources:
        return floors

    try:
        sync = ReviewSynchronization.model_validate(review_data["synchronization"])
        durations = {
            value["scan"]["id"]: value["scan"].get("duration_seconds")
            for value in review_data["scans"]
        }
    except (KeyError, TypeError, ValueError):
        return floors + [_required_interval(source, None, None, source) for source in new_sources]
    for source in new_sources:
        classification = (
            ReviewFilter.model_validate(saved[source.id]) if source.id in saved else source
        )
        severity = policy.severities.get(classification.subcategory, classification.severity)
        classification = classification.model_copy(
            update={"severity": policy.severities.get(source.subcategory, severity)}
        )
        try:
            start, end = evidence_interval(
                SimpleNamespace(**bindings[source.id]),
                sync,
                duration_seconds=durations.get(source.scan_id),
            )
        except (ArithmeticError, AttributeError, KeyError, TypeError, ValueError):
            # The saved interval cannot establish timing for an unproven source cue.
            start, end = None, None
        floors.append(_required_interval(source, start, end, classification))
    return floors


def _required_interval(floor, start, end, classification):
    return _RequiredInterval(
        start,
        end,
        floor.edit_action,
        (classification.subcategory, classification.severity, channel_for_source(floor.source)),
    )


def _published_coverage(export_format, content):
    groups = defaultdict(set)
    if export_format == ExportFormat.EDL:
        if content == b"## Editarr Simple EDL":
            return {}
        for entry in parse_edl_content(content).filters:
            interval = (entry.start_seconds, entry.end_seconds)
            groups[EditAction.MUTE].add(interval)
            if entry.edit_action == EditAction.SKIP:
                groups[EditAction.SKIP].add(interval)
    else:
        for entry in parse_mcf_content(content).entries:
            for channel in CHANNEL_COVERAGE[entry.mcf_channel]:
                groups[entry.subcategory, entry.severity, channel].add(
                    (entry.start_seconds, entry.end_seconds)
                )
    return {key: _merge_ranges(values) for key, values in groups.items()}


def _merge_ranges(values):
    merged = []
    for start, end in sorted(values):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _covered(coverage, export_format, floor):
    if floor.start is None or (floor.end is None and export_format == ExportFormat.MCF):
        return False
    start, end = policy_export_interval(floor.start, floor.end)
    if export_format == ExportFormat.EDL:
        key = floor.edl_action
        end = end if end is not None else start + 1
    else:
        key = floor.mcf_key
    intervals = coverage.get(key, ())
    position = bisect_right(intervals, (start, float("inf"))) - 1
    return position >= 0 and intervals[position][1] >= end
