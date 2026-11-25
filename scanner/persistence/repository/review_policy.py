"""Current-owner effective review content, separate from editable saved values."""

from dataclasses import dataclass
from fractions import Fraction
from functools import wraps
from math import inf, nextafter
from sys import float_info

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import SavedReviewRow, ScanFindingRow, ScanRow
from scanner.mcf import TimingInterval, map_affine_range
from scanner.models import (
    PROVENANCE_FIELDS,
    ReviewFilter,
    ReviewFilterInput,
    ReviewSynchronization,
)
from scanner.settings.filter_defaults import EnforcedFilterPolicy, enforce_filter_policy

from ..write_lock import write_lock
from .filter_preferences import enforced_filter_policy
from .review_policy_evidence import covers_evidence, required_evidence, snap_evidence


def policy_write(operation):
    """Read policy and publish under the same SQLite writer admission."""

    @wraps(operation)
    def locked(db, *args, **kwargs):
        with write_lock(db):
            return operation(db, *args, **kwargs)

    return locked


@dataclass(frozen=True)
class PreparedReviewPolicy:
    owner_id: int | None
    policy: EnforcedFilterPolicy
    base_filters: tuple[ReviewFilter, ...]
    filters: tuple[ReviewFilter, ...]
    required_ids: frozenset[str] = frozenset()
    missing_evidence: tuple[ReviewFilter, ...] = ()

    @property
    def policy_revision(self) -> int:
        return self.policy.policy_revision


class ReviewPolicyConflict(ValueError):
    """A policy-sensitive draft or publication must be reloaded, not silently weakened."""

    def __init__(self, message: str, code: str = "stale_review_policy") -> None:
        super().__init__(message)
        self.code = code


def _affine_roundoff_bound(*operands: float | None) -> float:
    """Allow two affine evaluations' roundoff at operand scale, including cancellation."""
    return 16 * float_info.epsilon * max(abs(value) for value in operands if value is not None)


def _map_policy_floor(start, end, source, target, *, duration_seconds=None):
    """Round exact affine minimums outward so coverage composes across every save."""
    identity = TimingInterval(0.0, 1.0)
    map_affine_range(start, end, identity, identity)
    map_affine_range(source.start_seconds, source.end_seconds, source, source)
    map_affine_range(
        target.start_seconds, target.end_seconds, target, target, duration_seconds=duration_seconds
    )
    source_start = Fraction(source.start_seconds)
    target_start = Fraction(target.start_seconds)
    scale = (Fraction(target.end_seconds) - target_start) / (
        Fraction(source.end_seconds) - source_start
    )
    mapped = []
    for endpoint, direction in ((start, -inf), (end, inf)):
        if endpoint is None:
            mapped.append(None)
            continue
        exact = target_start + (Fraction(endpoint) - source_start) * scale
        if exact < 0 or (duration_seconds is not None and exact > duration_seconds):
            raise ReviewPolicyConflict(
                "Synchronization cannot preserve required evidence within the media runtime.",
                "review_policy_timing_precision",
            )
        rounded = float(exact)
        if (direction < 0 and Fraction(rounded) > exact) or (
            direction > 0 and Fraction(rounded) < exact
        ):
            rounded = nextafter(rounded, direction)
        if rounded < 0 or (duration_seconds is not None and rounded > duration_seconds):
            raise ReviewPolicyConflict(
                "Synchronization cannot preserve required evidence within the media runtime.",
                "review_policy_timing_precision",
            )
        mapped.append(rounded)
    return tuple(mapped)


def owner_policy(db: Session, owner_id: int | None) -> EnforcedFilterPolicy:
    return enforced_filter_policy(db, owner_id) if owner_id is not None else EnforcedFilterPolicy(0)


def require_policy_revision(prepared, revision: int | None, owner_id: int | None = None):
    """Legacy requests are accepted only before the owner's first policy change."""
    if (
        (revision is None and prepared.policy_revision != 0)
        or (revision is not None and revision != prepared.policy_revision)
        or (owner_id is not None and owner_id != prepared.owner_id)
        or (prepared.policy_revision != 0 and owner_id != prepared.owner_id)
    ):
        raise ReviewPolicyConflict("The review owner's policy changed. Reload the review.")


def assert_current_policy(db: Session, review_id: str, prepared: PreparedReviewPolicy) -> None:
    row = db.execute(
        select(SavedReviewRow.owner_id).where(SavedReviewRow.id == review_id)
    ).one_or_none()
    if (
        row is None
        or row.owner_id != prepared.owner_id
        or owner_policy(db, row.owner_id).policy_revision != prepared.policy_revision
    ):
        raise ReviewPolicyConflict("The review owner's policy changed during preparation.")


def require_policy_evidence(prepared: PreparedReviewPolicy) -> None:
    """Refuse publication until the editable review's required evidence is restored."""
    if prepared.missing_evidence:
        raise ReviewPolicyConflict(
            "Required policy evidence is unavailable. Restore it before publication.",
            "review_policy_evidence_missing",
        )


def _source_filter(finding: ScanFindingRow, filter_id: str) -> ReviewFilter:
    return ReviewFilter(
        id=filter_id,
        source_scan_id=finding.scan_id,
        source_finding_id=finding.id,
        **{
            key: getattr(finding, key)
            for key in ReviewFilter.model_fields
            if key not in PROVENANCE_FIELDS
        },
    )


def prepare_review_policy(
    db: Session, review: SavedReviewRow, *, filters=None
) -> PreparedReviewPolicy:
    """Derive one current-owner overlay without changing saved rows or source evidence."""
    owner_id = review.owner_id
    policy = owner_policy(db, owner_id)
    base = tuple(
        ReviewFilter.model_validate(value, from_attributes=True)
        for value in (review.filters if filters is None else filters)
    )
    active = bool(policy.rules or policy.severities)
    effective = []
    required = set()
    missing = []
    for saved in base:
        if saved.source_finding_id is None:
            scan = db.get(ScanRow, saved.source_scan_id) if saved.source_scan_id else None
            # Manual and community filters have no immutable source finding: the policy
            # applies to their saved values.
            manual = saved.community_source_id is not None or (
                scan is not None
                and scan.origin == "manual"
                and any(
                    value.id == saved.id
                    and value.source_scan_id == saved.source_scan_id
                    and value.source_finding_id is None
                    and value.source == saved.source
                    for value in review.filters
                )
            )
            if manual:
                applied = enforce_filter_policy([saved], policy).filters[0]
                floor = enforce_filter_policy(
                    [saved.model_copy(update={"selected": False})], policy
                ).filters[0]
                if floor.selected:
                    required.add(saved.id)
                effective.append(applied)
                continue
        finding = (
            db.get(ScanFindingRow, saved.source_finding_id) if saved.source_finding_id else None
        )
        if finding is None or (finding.scan_id, finding.source) != (
            saved.source_scan_id,
            saved.source,
        ):
            if active:
                missing.append(saved)
            effective.extend(enforce_filter_policy([saved], policy).filters)
            continue
        source = _source_filter(finding, saved.id).model_copy(update={"selected": False})
        source_view = enforce_filter_policy([source], policy).filters[0]
        saved_floor = enforce_filter_policy(
            [saved.model_copy(update={"selected": False})], policy
        ).filters[0]
        selected = saved.selected or source_view.selected or saved_floor.selected
        severity = (
            source_view.severity
            if source.subcategory in policy.severities
            else saved_floor.severity
        )
        if source_view.selected or saved_floor.selected:
            required.add(saved.id)
        effective.append(saved.model_copy(update={"selected": selected, "severity": severity}))
    if filters is None and active:
        for evidence in required_evidence(db, review, policy, review.synchronization):
            effective = [
                snap_evidence(value, evidence, review.synchronization)
                if value.source_finding_id == evidence.source_finding_id
                else value
                for value in effective
            ]
            if not any(covers_evidence(value, evidence) for value in effective):
                missing.append(evidence)
    prepared = PreparedReviewPolicy(
        owner_id,
        policy,
        base,
        tuple(effective),
        frozenset(required),
        tuple(missing),
    )
    assert_current_policy(db, review.id, prepared)
    return prepared


def validate_policy_draft(
    db: Session, review: SavedReviewRow, data, *, retime_result: bool = False
) -> list[ReviewFilterInput]:
    """Keep the editable base and require evidence coverage.

    Endpoints within the affine roundoff bound snap to the immutable source floor.
    Other edits still require exact coverage after reconciliation.
    """
    prepared = prepare_review_policy(db, review)
    require_policy_revision(prepared, data.policy_revision, data.policy_owner_id)
    incoming = {value.id: value for value in data.filters}
    roles = {value.scan_id: value.role for value in data.memberships}
    base = {value.id: value for value in prepared.base_filters}
    if (
        prepared.owner_id is not None
        and data.policy_owner_id is None
        and data.filter_basis == "effective"
    ):
        if any(
            value.id in base
            and any(
                getattr(value, field) != getattr(base[value.id], field)
                for field in ("selected", "severity")
            )
            for value in data.filters
        ):
            raise ReviewPolicyConflict("Effective edits require the current review owner's token.")
    synchronization = getattr(data, "synchronization", None) or data.current_synchronization
    stored_sync = ReviewSynchronization.model_validate(review.synchronization, from_attributes=True)
    from .repository_review_retime import _has_unknown_cue_provenance

    requirements = required_evidence(db, review, owner_policy(db, review.owner_id), synchronization)
    source_intervals = {value.source_finding_id: value for value in requirements}
    for evidence in requirements:
        proposed = next(
            (
                value
                for value in data.filters
                if value.source_finding_id == evidence.source_finding_id
            ),
            None,
        )
        previous = next(
            (value for value in review.memberships if value.scan_id == evidence.source_scan_id),
            None,
        )
        if proposed is not None:
            proposed = incoming[proposed.id] = snap_evidence(proposed, evidence, synchronization)
        role = "audio" if evidence.source == "audio" else "video"
        if (proposed is not None and not covers_evidence(proposed, evidence)) or (
            previous is not None and roles.get(evidence.source_scan_id) not in {role, "both"}
        ):
            raise ReviewPolicyConflict(
                "Required review evidence cannot be removed or relocated", "review_policy_enforced"
            )

    for current in prepared.filters:
        if current.id not in prepared.required_ids:
            continue
        proposed = incoming.get(current.id)
        interval = (base[current.id].start_seconds, base[current.id].end_seconds)
        membership = next(
            (value for value in review.memberships if value.scan_id == current.source_scan_id),
            None,
        )
        evidence = source_intervals.get(current.source_finding_id)
        if evidence is not None:
            interval = (evidence.start_seconds, evidence.end_seconds)
        elif (
            membership is not None
            and _has_unknown_cue_provenance(membership)
            and synchronization != stored_sync
        ):
            interval = _map_policy_floor(
                interval[0],
                interval[1],
                TimingInterval(stored_sync.start_seconds, stored_sync.end_seconds),
                TimingInterval(synchronization.start_seconds, synchronization.end_seconds),
                duration_seconds=membership.scan.duration_seconds,
            )
        if (
            proposed is not None
            and evidence is None
            and membership is not None
            and _has_unknown_cue_provenance(membership)
            and (synchronization != stored_sync or retime_result)
        ):
            snapped = {}
            for field, endpoint in zip(("start_seconds", "end_seconds"), interval):
                submitted = getattr(proposed, field)
                if (
                    endpoint is not None
                    and submitted is not None
                    and abs(submitted - endpoint)
                    <= _affine_roundoff_bound(
                        getattr(base[current.id], field),
                        submitted,
                        endpoint,
                        stored_sync.start_seconds,
                        stored_sync.end_seconds,
                        synchronization.start_seconds,
                        synchronization.end_seconds,
                    )
                ):
                    snapped[field] = endpoint
            proposed = incoming[current.id] = proposed.model_copy(update=snapped)
        if (
            proposed is None
            or (
                membership is not None
                and roles.get(current.source_scan_id)
                not in {"audio" if current.source == "audio" else "video", "both"}
            )
            or not covers_evidence(
                proposed,
                evidence
                or base[current.id].model_copy(
                    update={"start_seconds": interval[0], "end_seconds": interval[1]}
                ),
                tolerance_seconds=0,
            )
        ):
            raise ReviewPolicyConflict(
                "Required review evidence cannot be removed or relocated", "review_policy_enforced"
            )
    views = {value.id: value for value in prepared.filters}
    result = []
    for value in data.filters:
        value = incoming[value.id]
        updates = {}
        if data.filter_basis == "effective" and value.id in base:
            for field in ("selected", "severity"):
                if getattr(value, field) == getattr(views[value.id], field):
                    updates[field] = getattr(base[value.id], field)
        result.append(value.model_copy(update=updates))
    changed = prepare_review_policy(db, review, filters=result)
    if prepared.required_ids - changed.required_ids:
        raise ReviewPolicyConflict(
            "Required classification cannot be erased", "review_policy_enforced"
        )
    return result
