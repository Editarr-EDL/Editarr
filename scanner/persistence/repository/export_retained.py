"""Re-evaluate retained base values against their immutable publication evidence."""

from types import SimpleNamespace

from scanner.modeling.community_review_filters import classification
from scanner.models import (
    PROVENANCE_FIELDS,
    ExportFormat,
    ReviewFilter,
    SavedReview,
    ScanFinding,
)
from scanner.settings.filter_defaults import enforce_filter_policy

from .repository_mcf_preflight import _mcf_document_issues, _mcf_filter_issues
from .review_export_content import render_review_export
from .review_policy import PreparedReviewPolicy, ReviewPolicyConflict, owner_policy
from .review_policy_evidence import covers_evidence, evidence_interval, snap_evidence


def prepare_retained_export(db, row, snapshot):
    """Use only the saved base and source bindings; refuse unverifiable required evidence."""
    from .export_publication import SNAPSHOT_VERSION

    if snapshot["version"] != SNAPSHOT_VERSION or "base_filters" not in snapshot["review"]:
        raise ReviewPolicyConflict("Retained base values are unavailable")
    review = SavedReview.model_validate(snapshot["review"])
    policy = owner_policy(db, row.policy_owner_id)
    base = tuple(review.base_filters) + legacy_community_filters(snapshot["review"])
    effective = list(enforce_filter_policy(base, policy).filters)
    required_ids = set()
    sources = {
        finding["id"]: ScanFinding.model_validate(finding)
        for source in snapshot["sources"]
        for finding in source["findings"]
    }
    scans = {value.scan.id: value.scan for value in review.scans}
    active = bool(policy.rules or policy.severities)
    for position, saved in enumerate(base):
        source = sources.get(saved.source_finding_id)
        if source is None:
            scan = scans.get(saved.source_scan_id)
            # Manual and community filters have no source finding to re-evaluate.
            if active and not (
                saved.source_finding_id is None
                and (saved.community_source_id is not None or (scan and scan.origin == "manual"))
            ):
                raise ReviewPolicyConflict("Retained source evidence is unavailable")
        else:
            if (source.scan_id, source.source) != (saved.source_scan_id, saved.source):
                raise ReviewPolicyConflict("Retained source evidence does not match")
            source_view = enforce_filter_policy(
                [source.model_copy(update={"selected": False})], policy
            ).filters[0]
            effective[position] = effective[position].model_copy(
                update={
                    "selected": effective[position].selected or source_view.selected,
                    "severity": source_view.severity
                    if source.subcategory in policy.severities
                    else effective[position].severity,
                }
            )
            if source_view.selected:
                required_ids.add(saved.id)
        if (
            enforce_filter_policy([saved.model_copy(update={"selected": False})], policy)
            .filters[0]
            .selected
        ):
            required_ids.add(saved.id)
    for value in snapshot["policy_evidence"]:
        binding = SimpleNamespace(**value)
        finding = sources.get(binding.source_finding_id)
        if finding is None:
            if active:
                raise ReviewPolicyConflict("Retained policy evidence is unavailable")
            continue
        source = ReviewFilter(
            **{
                key: getattr(finding, key)
                for key in ReviewFilter.model_fields
                if key not in PROVENANCE_FIELDS
            },
            id=f"retained-{finding.id}",
            source_scan_id=finding.scan_id,
            source_finding_id=finding.id,
        ).model_copy(update={"selected": False})
        floor = enforce_filter_policy([source], policy).filters[0]
        if not floor.selected:
            continue
        scan = scans.get(finding.scan_id)
        if scan is None:
            raise ReviewPolicyConflict("Retained scan evidence is unavailable")
        start, end = evidence_interval(
            binding, review.synchronization, duration_seconds=scan.duration_seconds
        )
        floor = floor.model_copy(update={"start_seconds": start, "end_seconds": end})
        effective = [
            snap_evidence(value, floor, review.synchronization)
            if value.source_finding_id == floor.source_finding_id
            else value
            for value in effective
        ]
        covered = False
        for position, value in enumerate(effective):
            if covers_evidence(value, floor):
                effective[position] = value.model_copy(update={"selected": True})
                required_ids.add(value.id)
                covered = True
        if not covered:
            raise ReviewPolicyConflict("Retained review does not cover required evidence")
    if active and any(
        enforce_filter_policy([finding.model_copy(update={"selected": False})], policy)
        .filters[0]
        .selected
        and finding.id not in {value["source_finding_id"] for value in snapshot["policy_evidence"]}
        for finding in sources.values()
    ):
        raise ReviewPolicyConflict("Retained source has no immutable policy binding")
    prepared = PreparedReviewPolicy(
        row.policy_owner_id, policy.policy_revision, base, tuple(effective), frozenset(required_ids)
    )
    duration = min(
        (scan.duration_seconds for scan in scans.values() if scan.duration_seconds is not None),
        default=None,
    )
    if row.format == ExportFormat.MCF and (
        _mcf_document_issues(review.mcf_document, list(scans.values()))
        or any(
            _mcf_filter_issues(value, position, duration)
            for position, value in enumerate(effective)
            if value.selected
        )
    ):
        raise ReviewPolicyConflict("Retained MCF content is incomplete")
    if duration is None and not scans:
        duration = snapshot.get("duration_seconds")
    return render_review_export(review, prepared, row.format, duration)[0]


def legacy_community_filters(review_data: dict) -> tuple[ReviewFilter, ...]:
    """Review filters for the community snapshot filters of a retained snapshot taken
    before community filters were review filters (0040): those snapshots list them
    under `community_sources[].filters`, all enabled. Each is an enabled skip of both
    channels or an audio mute; a filter without a valid classification or wholly before
    the start is left out, as exports did then. Newer snapshots return nothing here."""
    if any(value.get("community_source_id") for value in review_data.get("base_filters", ())):
        return ()
    found = []
    for source in review_data.get("community_sources", ()):
        for value in source.get("filters", ()):
            category, subcategory = classification(value)
            if category is None or value["end_seconds"] <= max(0.0, value["start_seconds"]):
                continue
            mute = value["edit_action"] == "mute"
            found.append(
                ReviewFilter(
                    id=f"community-{source['source_id']}-{value['filter_id']}",
                    community_source_id=source["source_id"],
                    community_filter_id=value["filter_id"],
                    source="audio" if mute else "visual",
                    category=category,
                    subcategory=subcategory,
                    severity=value.get("severity") or None,
                    mcf_channel="audio" if mute else "both",
                    label=None,
                    details=None,
                    group_key=None,
                    start_seconds=max(0.0, value["start_seconds"]),
                    end_seconds=value["end_seconds"],
                    confidence=None,
                    edit_action=value["edit_action"],
                    selected=True,
                )
            )
    return tuple(found)
