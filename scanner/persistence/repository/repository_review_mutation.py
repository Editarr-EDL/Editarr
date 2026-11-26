"""Saved-review revision, retiming, mutation, and duplication operations."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    ReviewAcceptedCreditsRow,
    ReviewFilterRow,
    ReviewScanMembershipRow,
    SavedReviewRow,
)
from scanner.models import (
    ReviewDuplicate,
    ReviewMcfDocument,
    ReviewRename,
    ReviewSave,
    ReviewScanMembership,
    SavedReview,
)
from scanner.repository import (
    ReviewRevisionConflict,
)

from ..write_lock import write_lock
from .activity_ownership import new_review_owner_columns
from .media_identity_store import bind_review_version, review_version_id
from .repository_mcf_preflight import (
    _signature_value,
    filter_mcf_value,
)
from .repository_review_retime import (
    _map_retime_entries as _map_retime_entries,
)
from .repository_review_retime import (
    _retime_accepted_credits as _retime_accepted_credits,
)
from .repository_review_retime import (
    _review_retime_runtime as _review_retime_runtime,
)
from .repository_review_retime import (
    _validate_retime_filter_provenance as _validate_retime_filter_provenance,
)
from .repository_review_retime import (
    retime_saved_review_draft as retime_saved_review_draft,
)
from .review_community_filters import review_filter_rows
from .review_community_sources import copy_sources, kept_plans, prepare_sources
from .review_filter_tracking import track_manual_edits
from .review_policy import (
    policy_write,
    prepare_review_policy,
    require_policy_revision,
    validate_policy_draft,
)
from .review_policy_evidence import bind_review_evidence, copy_review_evidence


def _revision_signatures(
    memberships,
    filters,
    mcf_document,
    synchronization,
    accepted_credits,
) -> tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...]]:
    """Compute field signatures used to advance format-specific revisions."""
    membership_signature = tuple(
        (membership.scan_id, _signature_value(membership.role)) for membership in memberships
    )

    def filter_signature(fields, field_value=getattr):
        return tuple(
            tuple(_signature_value(field_value(review_filter, field)) for field in fields)
            for review_filter in filters
        )

    document_signature = tuple(
        _signature_value(getattr(mcf_document, field, None)) for field in root._MCF_DOCUMENT_FIELDS
    )
    synchronization_signature = (
        synchronization.start_seconds,
        synchronization.end_seconds,
        _signature_value(synchronization.end_mode),
    )
    aggregate = (
        membership_signature,
        filter_signature(root._AGGREGATE_FILTER_FIELDS),
        document_signature,
        synchronization_signature,
        root._accepted_credits_signature(accepted_credits),
    )
    edl = (membership_signature, filter_signature(root._EDL_FILTER_FIELDS))
    mcf = (
        membership_signature,
        filter_signature(root._MCF_FILTER_FIELDS, filter_mcf_value),
        document_signature,
    )
    return aggregate, edl, mcf


def _check_review_revision(db: Session, row: SavedReviewRow, revision: int) -> None:
    persisted_revision = db.scalar(
        select(SavedReviewRow.revision).where(SavedReviewRow.id == row.id)
    )
    if persisted_revision != revision:
        raise ReviewRevisionConflict("This review was updated elsewhere. Reload it before saving.")
    db.refresh(row)


def _claim_review_revision(
    db: Session,
    review_id: str,
    revision: int,
    values: dict[str, object] | None = None,
) -> None:
    """Atomically claim the expected review revision before mutation."""
    claimed = db.execute(
        update(SavedReviewRow)
        .where(SavedReviewRow.id == review_id, SavedReviewRow.revision == revision)
        .values(**(values or {"revision": revision}))
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise ReviewRevisionConflict("This review was updated elsewhere. Reload it before saving.")


def save_saved_review(
    db: Session,
    review_id: str,
    data: ReviewSave,
    *,
    target_source: str | None = None,
    target_item_id: str | None = None,
) -> SavedReview | None:
    """Replace a review, rolling back staged Manual evidence on any failure."""
    try:
        with write_lock(db):
            return _save_saved_review(
                db,
                review_id,
                data,
                target_source=target_source,
                target_item_id=target_item_id,
            )
    except Exception:
        db.rollback()
        raise


def _save_saved_review(
    db: Session,
    review_id: str,
    data: ReviewSave,
    *,
    target_source: str | None = None,
    target_item_id: str | None = None,
) -> SavedReview | None:
    """Validate and persist a review draft with optimistic revision checks."""
    row = db.get(SavedReviewRow, review_id)
    if row is None:
        return None
    _check_review_revision(db, row, data.revision)
    bind_review_evidence(db, row)
    data = data.model_copy(update={"filters": validate_policy_draft(db, row, data)})
    requested_memberships = list(data.memberships)
    manual_scan, manual_filters = root._append_manual_review_filters(
        db,
        media_item_id=row.media_item_id,
        media_source_id=row.media_source_id,
        values=data.new_manual_filters,
        owner_id=row.owner_id,
        stable_version_id=review_version_id(db, row.id),
    )
    if manual_scan is not None:
        requested_memberships = root._with_manual_membership(
            requested_memberships, manual_scan.id, manual_filters
        )
    memberships = root._validate_memberships(
        db,
        media_item_id=row.media_item_id,
        media_source_id=row.media_source_id,
        memberships=requested_memberships,
    )
    root._validate_review_synchronization_runtime(row, data.synchronization, memberships)
    root._validate_review_mcf_runtime(data.mcf_document, memberships, review=row)
    root._validate_synchronization_document(data.synchronization, data.mcf_document)
    inputs = list(data.filters) + manual_filters
    plans = prepare_sources(db, row, data.community_memberships)
    old_signatures = _revision_signatures(
        row.memberships,
        row.filters,
        row.mcf_document,
        row.synchronization,
        row.accepted_credits,
    )
    old_name = row.name
    new_filters = review_filter_rows(
        db, row, memberships, inputs, kept_plans(row) if plans is None else plans
    )
    track_manual_edits(db, row, new_filters, data.synchronization, memberships)
    new_signatures = _revision_signatures(
        requested_memberships,
        new_filters,
        data.mcf_document,
        data.synchronization,
        data.accepted_credits,
    )
    accepted_credits = root._accepted_credits_row_for_save(
        db,
        row,
        row.accepted_credits,
        data.accepted_credits,
        requested_memberships,
        inputs,
    )
    community_changed = plans is not None
    content_changed = old_signatures[0] != new_signatures[0] or community_changed
    edl_changed = old_signatures[1] != new_signatures[1] or community_changed
    mcf_changed = old_signatures[2] != new_signatures[2] or community_changed
    name_changed = old_name != data.name
    if not content_changed and not name_changed:
        _claim_review_revision(db, row.id, data.revision)
        db.rollback()
        return root._review_detail(db, row, source=target_source, item_id=target_item_id)
    now = datetime.now(timezone.utc)
    _claim_review_revision(
        db,
        row.id,
        data.revision,
        {
            "name": data.name,
            "revision": data.revision + 1,
            "content_revision": row.content_revision + int(content_changed),
            "edl_content_revision": row.edl_content_revision + int(edl_changed),
            "mcf_content_revision": row.mcf_content_revision + int(mcf_changed),
            "updated_at": now,
        },
    )
    row.memberships.clear()
    row.filters.clear()
    db.flush()
    if plans is not None:
        # Store the snapshots before the filters that reference them.
        row.community_sources = [plan.row for plan in plans]
        db.flush()
    row.memberships = list(memberships.values())
    row.filters = new_filters
    row.mcf_document = root._review_mcf_document_row(row.id, data.mcf_document)
    row.synchronization = root._review_synchronization_row(row.id, data.synchronization)
    row.accepted_credits = accepted_credits
    try:
        bind_review_evidence(db, row)
        prepare_review_policy(db, row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return root._review_detail(db, row, source=target_source, item_id=target_item_id)


def rename_saved_review(
    db: Session,
    review_id: str,
    data: ReviewRename,
    *,
    target_source: str | None = None,
    target_item_id: str | None = None,
) -> SavedReview | None:
    """Rename a review if its revision matches, retaining its content revisions."""
    row = db.get(SavedReviewRow, review_id)
    if row is None:
        return None
    _check_review_revision(db, row, data.revision)
    if row.name != data.name:
        _claim_review_revision(
            db,
            row.id,
            data.revision,
            {
                "name": data.name,
                "revision": data.revision + 1,
                "updated_at": datetime.now(timezone.utc),
            },
        )
        db.commit()
        db.refresh(row)
    else:
        _claim_review_revision(db, row.id, data.revision)
        db.rollback()
    return root._review_detail(db, row, source=target_source, item_id=target_item_id)


@policy_write
def duplicate_saved_review(
    db: Session,
    review_id: str,
    data: ReviewDuplicate,
    *,
    target_source: str | None = None,
    target_item_id: str | None = None,
) -> SavedReview | None:
    """Copy a review and its content into a new, independent draft."""
    source = db.get(SavedReviewRow, review_id)
    if source is None:
        return None
    _check_review_revision(db, source, data.revision)
    require_policy_revision(
        prepare_review_policy(db, source), data.policy_revision, data.policy_owner_id
    )
    _claim_review_revision(db, source.id, data.revision)
    source_version = review_version_id(db, source.id)
    if source_version is None:
        root._validate_memberships(
            db,
            media_item_id=source.media_item_id,
            media_source_id=source.media_source_id,
            memberships=[
                ReviewScanMembership(scan_id=value.scan_id, role=value.role)
                for value in source.memberships
            ],
        )
    duplicate = SavedReviewRow(
        id=str(uuid.uuid4()),
        media_item_id=source.media_item_id,
        media_source_id=source.media_source_id,
        name=data.name,
        owner_id=new_review_owner_columns(db)["owner_id"],
        original_author_id=source.original_author_id,
    )
    db.add(duplicate)
    db.flush()
    if source_version is not None:
        bind_review_version(db, duplicate.id, source_version)
    duplicate.memberships = [
        ReviewScanMembershipRow(
            scan_id=membership.scan_id, role=membership.role, position=membership.position
        )
        for membership in source.memberships
    ]
    source_ids = copy_sources(source, duplicate)
    db.flush()
    duplicate.filters = [
        ReviewFilterRow(
            review_id=duplicate.id,
            id=str(uuid.uuid4()),
            source_scan_id=review_filter.source_scan_id,
            source_finding_id=review_filter.source_finding_id,
            position=review_filter.position,
            source=review_filter.source,
            category=review_filter.category,
            subcategory=review_filter.subcategory,
            severity=review_filter.severity,
            mcf_channel=review_filter.mcf_channel,
            mcf_comment=review_filter.mcf_comment,
            label=review_filter.label,
            details=review_filter.details,
            group_key=review_filter.group_key,
            start_seconds=review_filter.start_seconds,
            end_seconds=review_filter.end_seconds,
            confidence=review_filter.confidence,
            edit_action=review_filter.edit_action,
            selected=review_filter.selected,
            manually_added=review_filter.manually_added,
            manually_edited=review_filter.manually_edited,
            community_source_id=source_ids.get(review_filter.community_source_id),
            community_filter_id=review_filter.community_filter_id,
            community_edited=review_filter.community_edited,
        )
        for review_filter in source.filters
    ]
    source_document = (
        ReviewMcfDocument.model_validate(source.mcf_document, from_attributes=True)
        if source.mcf_document is not None
        else ReviewMcfDocument()
    )
    duplicate.mcf_document = root._review_mcf_document_row(duplicate.id, source_document)
    duplicate.synchronization = root._review_synchronization_row(
        duplicate.id, root._review_synchronization(source.synchronization)
    )
    if source.accepted_credits is not None:
        accepted = source.accepted_credits
        duplicate.accepted_credits = ReviewAcceptedCreditsRow(
            review_id=duplicate.id,
            scan_id=accepted.scan_id,
            source_finding_id=accepted.source_finding_id,
            candidate_id=accepted.candidate_id,
            detected_boundary_seconds=accepted.detected_boundary_seconds,
            accepted_boundary_seconds=accepted.accepted_boundary_seconds,
            confidence=accepted.confidence,
            method=accepted.method,
            accepted_at=accepted.accepted_at,
        )
    copy_review_evidence(db, source, duplicate)
    prepare_review_policy(db, duplicate)
    db.commit()
    db.refresh(duplicate)
    return root._review_detail(db, duplicate, source=target_source, item_id=target_item_id)


def remove_review_row(db: Session, row: SavedReviewRow, revision: int) -> None:
    """Stage deleting a review whose export rows were already disposed of."""
    _check_review_revision(db, row, revision)
    _claim_review_revision(db, row.id, revision)
    db.delete(row)


def delete_saved_review(db: Session, review_id: str, revision: int) -> bool:
    """Delete an unpublished review by identifier after checking the expected revision.

    A review that any export row still names needs `review_disposition` instead.
    """
    from scanner.db import MediaItemExportRow

    from .review_deletion import PublishedReviewDeletionBlocked

    row = db.get(SavedReviewRow, review_id)
    if row is None:
        return False
    _check_review_revision(db, row, revision)
    if db.query(MediaItemExportRow.id).filter_by(review_id=review_id).first() is not None:
        db.rollback()
        raise PublishedReviewDeletionBlocked()
    remove_review_row(db, row, revision)
    db.commit()
    return True
