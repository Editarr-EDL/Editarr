"""Explicit Apply consumes a current owner's base draft, never its enforced view."""

from scanner.db import SavedReviewRow
from scanner.modeling.model_reviews import ReviewFilterDefaultsApplyResult
from scanner.models import ReviewFilterInput
from scanner.settings.filter_defaults import EnforcedFilterPolicy, apply_filter_defaults

from .filter_preferences import (
    base_filter_settings,
    filter_preferences_view,
)
from .review_policy import PreparedReviewPolicy, prepare_review_policy, require_policy_revision


def apply_new_defaults(db, access, request, session_id):
    """Prepare a not-yet-saved own draft from one revision-consistent preferences view."""
    from scanner.security.access.api_shapes import AccessErrorCode
    from scanner.security.access.authorization import require_principal
    from scanner.security.access.capability_ids import Capability as C
    from scanner.security.access.decisions import AccessDenied

    principal = access.principal
    fresh = require_principal(db, principal.user_id, principal.credential, session_id)
    if C.REVIEWS_FILTERS_EDIT_OWN not in fresh.capabilities:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    view = filter_preferences_view(db, principal.user_id)
    if request.policy_revision is not None or request.policy_owner_id is not None:
        policy = EnforcedFilterPolicy(
            view.policy_revision,
            tuple(entry.rule for entry in view.rules if entry.enforced),
            {entry.subcategory: entry.severity for entry in view.severities if entry.enforced},
        )
        require_policy_revision(
            PreparedReviewPolicy(principal.user_id, policy, (), ()),
            request.policy_revision,
            request.policy_owner_id,
        )
    result = apply_filter_defaults(request.filters, view.settings())
    base = apply_filter_defaults(request.filters, base_filter_settings(db, principal.user_id))
    return ReviewFilterDefaultsApplyResult(
        **result.model_dump(),
        base_filters=base.filters,
        policy_owner_id=principal.user_id,
        policy_revision=view.policy_revision,
    )


def apply_review_defaults(db, review_id: str, request):
    """Return Applied copies using the review owner's defaults and immutable policy evidence."""
    review = db.get(SavedReviewRow, review_id)
    if review is None:
        return None
    prepared = prepare_review_policy(db, review)
    require_policy_revision(prepared, request.policy_revision, request.policy_owner_id)
    from .repository_review_core import _filter_rows_from_inputs

    _filter_rows_from_inputs(
        db,
        review_id=review_id,
        memberships={value.scan_id: value for value in review.memberships},
        # Community rows are already on the review's timeline. Apply reads only their submitted
        # values, and the save validates their snapshot or live-link provenance.
        filters=[value for value in request.filters if value.community_source_id is None],
    )
    settings = base_filter_settings(db, prepared.owner_id)
    applied = apply_filter_defaults(request.filters, settings)
    effective = prepare_review_policy(db, review, filters=applied.filters)
    return ReviewFilterDefaultsApplyResult.model_validate(
        {
            **applied.model_dump(),
            "base_filters": applied.filters,
            "filters": [
                ReviewFilterInput.model_validate(value.model_dump()) for value in effective.filters
            ],
            "selection_changes": sum(
                before.selected != after.selected
                for before, after in zip(request.filters, effective.filters, strict=True)
            ),
            "severity_changes": sum(
                before.severity != after.severity
                for before, after in zip(request.filters, effective.filters, strict=True)
            ),
            "policy_owner_id": effective.owner_id,
            "policy_revision": effective.policy_revision,
        }
    )
