"""Durable source bindings and their mapped interval coverage floor."""

from fractions import Fraction
from math import ceil, floor

from sqlalchemy import select

from scanner.db import ReviewPolicyEvidenceRow, ScanFindingRow, ScanRow
from scanner.mcf import TimingInterval

ACTION_COVERAGE = {"mute": frozenset({"mute"}), "skip": frozenset({"mute", "skip"})}
CHANNEL_COVERAGE = {
    "audio": frozenset({"audio"}),
    "video": frozenset({"video"}),
    "both": frozenset({"audio", "video", "both"}),
}


def _required_source(db, binding, policy):
    from scanner.settings.filter_defaults import enforce_filter_policy

    from .review_policy import _source_filter

    finding = db.get(ScanFindingRow, binding.source_finding_id)
    source = _source_filter(finding, f"policy-{finding.scan_id}-{finding.id}")
    source = source.model_copy(update={"selected": False})
    return source if enforce_filter_policy([source], policy).filters[0].selected else None


def evidence_interval(binding, synchronization, *, duration_seconds=None):
    """Map a first-bound interval without adopting subsequent editable cue times."""
    from .review_policy import _map_policy_floor

    interval = (binding.start_seconds, binding.end_seconds)
    if binding.retime and (binding.sync_start, binding.sync_end) != (
        synchronization.start_seconds,
        synchronization.end_seconds,
    ):
        return _map_policy_floor(
            *interval,
            TimingInterval(binding.sync_start, binding.sync_end),
            TimingInterval(synchronization.start_seconds, synchronization.end_seconds),
            duration_seconds=duration_seconds,
        )
    return interval


def review_bindings(db, review):
    """Keep current membership evidence and only currently required detached bindings."""
    from .repository_review_core import _snapshot_findings
    from .repository_review_retime import _has_unknown_cue_provenance
    from .review_policy import owner_policy

    bindings = {
        value.source_finding_id: value
        for value in db.scalars(
            select(ReviewPolicyEvidenceRow).where(ReviewPolicyEvidenceRow.review_id == review.id)
        )
    }
    sync = review.synchronization
    current = set()
    for snapshot in _snapshot_findings(
        db,
        {value.scan_id: value for value in review.memberships},
        media_item_id=review.media_item_id,
        media_source_id=review.media_source_id,
    ):
        current.add(snapshot.source_finding_id)
        if snapshot.source_finding_id in bindings:
            continue
        membership = next(
            value for value in review.memberships if value.scan_id == snapshot.source_scan_id
        )
        scan = membership.scan
        retime = _has_unknown_cue_provenance(membership)
        interval = (snapshot.start_seconds, snapshot.end_seconds)
        document = scan.mcf_document
        source_start = document.material_start_seconds if document else 0
        source_end = document.material_end_seconds if document else scan.duration_seconds
        basis = (sync.start_seconds, sync.end_seconds)
        if retime and source_end is not None and source_end > source_start:
            basis = (source_start, source_end)
        bindings[snapshot.source_finding_id] = ReviewPolicyEvidenceRow(
            review_id=review.id,
            source_finding_id=snapshot.source_finding_id,
            start_seconds=interval[0],
            end_seconds=interval[1],
            sync_start=basis[0],
            sync_end=basis[1],
            retime=retime,
        )
    policy = owner_policy(db, review.owner_id)
    return tuple(
        value
        for finding_id, value in bindings.items()
        if finding_id in current or _required_source(db, value, policy) is not None
    )


def bind_review_evidence(db, review):
    """Reconcile bindings in the admitted transaction without retaining optional history."""
    bindings = review_bindings(db, review)
    retained = {value.source_finding_id for value in bindings}
    for value in db.scalars(
        select(ReviewPolicyEvidenceRow).where(ReviewPolicyEvidenceRow.review_id == review.id)
    ):
        if value.source_finding_id not in retained:
            db.delete(value)
    db.add_all(bindings)


def copy_review_evidence(db, source, duplicate):
    """A new owner receives the same immutable basis, not the previous owner's overlay."""
    from .review_policy import owner_policy

    db.flush()
    current = {value.source_finding_id for value in review_bindings(db, duplicate)}
    policy = owner_policy(db, duplicate.owner_id)
    db.add_all(
        [
            ReviewPolicyEvidenceRow(
                review_id=duplicate.id,
                source_finding_id=value.source_finding_id,
                start_seconds=value.start_seconds,
                end_seconds=value.end_seconds,
                sync_start=value.sync_start,
                sync_end=value.sync_end,
                retime=value.retime,
            )
            for value in review_bindings(db, source)
            if value.source_finding_id in current or _required_source(db, value, policy) is not None
        ]
    )
    db.flush()


def required_evidence(db, review, policy, synchronization):
    """Evaluate immutable source metadata under the current owner's policy."""
    required = []
    for binding in review_bindings(db, review):
        source = _required_source(db, binding, policy)
        if source is not None:
            scan = db.get(ScanRow, source.source_scan_id)
            interval = evidence_interval(
                binding, synchronization, duration_seconds=scan.duration_seconds
            )
            required.append(
                source.model_copy(update={"start_seconds": interval[0], "end_seconds": interval[1]})
            )
    return required


def snap_evidence(proposed, required, synchronization):
    """Reconcile roundoff against a fixed source floor, never a previous saved cue."""
    from .review_policy import _affine_roundoff_bound

    updates = {}
    for field in ("start_seconds", "end_seconds"):
        endpoint = getattr(required, field)
        submitted = getattr(proposed, field)
        if (
            endpoint is not None
            and submitted is not None
            and abs(submitted - endpoint)
            <= _affine_roundoff_bound(
                submitted, endpoint, synchronization.start_seconds, synchronization.end_seconds
            )
        ):
            updates[field] = endpoint
    return proposed.model_copy(update=updates)


def covers_evidence(proposed, required, *, tolerance_seconds=0):
    """Cover provenance, channel, action, and the exact required interval.

    EDL subtracts skips from mutes because skipping removes both sound and picture.
    No other action substitution is inferred; MCF channels must independently cover.
    Mapped floors are rounded outward before comparison, never relaxed inward.
    """
    return (
        proposed.source_scan_id == required.source_scan_id
        and proposed.source_finding_id == required.source_finding_id
        and proposed.source == required.source
        and required.edit_action
        in ACTION_COVERAGE.get(proposed.edit_action, {proposed.edit_action})
        and (required.mcf_channel or ("audio" if required.source == "audio" else "video"))
        in CHANNEL_COVERAGE.get(proposed.mcf_channel or "both", ())
        and proposed.start_seconds <= required.start_seconds + tolerance_seconds
        and (
            required.end_seconds is None
            or (
                proposed.end_seconds is not None
                and proposed.end_seconds + tolerance_seconds >= required.end_seconds
            )
        )
    )


def policy_export_filters(prepared, *, duration_seconds=None):
    """Enclose required intervals on the EDL/MCF millisecond grid without mutating base rows."""
    filters = []
    for value in prepared.filters:
        if value.id in prepared.required_ids:
            start, end = policy_export_interval(
                value.start_seconds, value.end_seconds, duration_seconds=duration_seconds
            )
            value = value.model_copy(
                update={
                    "start_seconds": start,
                    "end_seconds": end,
                }
            )
        filters.append(value)
    return filters


def policy_export_interval(start, end, *, duration_seconds=None):
    """Enclose a required output interval at millisecond precision, or refuse atomically."""
    from .review_policy import ReviewPolicyConflict

    start = Fraction(floor(Fraction(start) * 1000), 1000)
    end = Fraction(ceil(Fraction(end) * 1000), 1000) if end is not None else None
    if end is not None and duration_seconds is not None and end > duration_seconds:
        raise ReviewPolicyConflict(
            "Required evidence cannot fit the output precision within the media runtime.",
            "review_policy_timing_precision",
        )
    return float(start), float(end) if end is not None else None
