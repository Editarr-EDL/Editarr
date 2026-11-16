"""Build complete draft retiming requests for repository tests."""

from uuid import uuid4

from scanner.models import (
    EditAction,
    NewManualReviewFilter,
    ReviewFilterInput,
    ReviewRetimeRequest,
    ReviewSynchronization,
)


def _request(review, **updates):
    values = {
        "revision": review.revision,
        "memberships": review.memberships,
        "current_synchronization": review.synchronization,
        "proposed_synchronization": ReviewSynchronization(
            start_seconds=10, end_seconds=90, end_mode="before_credits"
        ),
        "mcf_document": review.mcf_document,
        "filters": [ReviewFilterInput(**value.model_dump()) for value in review.filters],
        "accepted_credits": review.accepted_credits,
        "new_manual_filters": [
            NewManualReviewFilter(
                id=str(uuid4()),
                source="audio",
                category="language",
                subcategory="swearing",
                label="Manual",
                details="Edited draft",
                group_key="manual",
                start_seconds=50,
                end_seconds=60,
                edit_action=EditAction.MUTE,
                selected=True,
            )
        ],
    }
    return ReviewRetimeRequest(**(values | updates))

