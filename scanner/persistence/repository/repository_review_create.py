"""Saved-review creation, metadata, and synchronization validation."""

import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    MediaItemRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMediaVersionRow,
    ScanRow,
)
from scanner.models import (
    NewManualReviewFilter,
    ReviewCreate,
    ReviewFilterInput,
    ReviewMcfDocument,
    SavedReview,
    ScanOrigin,
)
from scanner.repository import (
    ReviewMembershipError,
)

from .activity_ownership import new_review_owner_columns, new_scan_initiator_columns
from .filter_preferences import base_filter_settings
from .media_identity_store import bind_review_version, bind_scan_version, get_version
from .repository_paths import (
    resolve_local_path,
)
from .repository_review_metadata import (
    _accepted_credits_row_for_save as _accepted_credits_row_for_save,
)
from .repository_review_metadata import (
    _accepted_credits_signature as _accepted_credits_signature,
)
from .repository_review_metadata import (
    _accepted_credits_snapshot as _accepted_credits_snapshot,
)
from .repository_review_metadata import (
    _catalog_review_mcf_document as _catalog_review_mcf_document,
)
from .repository_review_metadata import (
    _default_review_synchronization as _default_review_synchronization,
)
from .repository_review_metadata import (
    _document_for_synchronization as _document_for_synchronization,
)
from .repository_review_metadata import (
    _fill_mcf_document_gaps as _fill_mcf_document_gaps,
)
from .repository_review_metadata import (
    _review_mcf_document_row as _review_mcf_document_row,
)
from .repository_review_metadata import (
    _review_synchronization_row as _review_synchronization_row,
)
from .repository_review_metadata import (
    _seed_review_mcf_document as _seed_review_mcf_document,
)
from .repository_review_metadata import (
    _validate_review_mcf_runtime as _validate_review_mcf_runtime,
)
from .repository_review_metadata import (
    _validate_review_synchronization_runtime as _validate_review_synchronization_runtime,
)
from .repository_review_metadata import (
    _validate_synchronization_document as _validate_synchronization_document,
)
from .repository_review_metadata import (
    _with_manual_membership as _with_manual_membership,
)
from .review_community_filters import review_filter_rows
from .review_community_sources import prepare_sources
from .review_policy import policy_write, prepare_review_policy
from .review_policy_evidence import bind_review_evidence
from .review_version_evidence import version_file_matches


@policy_write
def create_saved_review(
    db: Session,
    *,
    media_item_id: str,
    media_source_id: str | None,
    duration_seconds: float | None = None,
    data: ReviewCreate,
    catalog_mcf_document: ReviewMcfDocument | None = None,
    target_source: str | None = None,
    target_item_id: str | None = None,
    stable_version_id: str | None = None,
) -> SavedReview:
    """Create a review, rolling back every staged row on any failure."""
    try:
        return _create_saved_review(
            db,
            media_item_id=media_item_id,
            media_source_id=media_source_id,
            duration_seconds=duration_seconds,
            data=data,
            catalog_mcf_document=catalog_mcf_document,
            target_source=target_source,
            target_item_id=target_item_id,
            stable_version_id=stable_version_id,
        )
    except Exception:
        db.rollback()
        raise


def _create_saved_review(
    db: Session,
    *,
    media_item_id: str,
    media_source_id: str | None,
    duration_seconds: float | None,
    data: ReviewCreate,
    catalog_mcf_document: ReviewMcfDocument | None = None,
    target_source: str | None = None,
    target_item_id: str | None = None,
    stable_version_id: str | None = None,
) -> SavedReview:
    """Persist a new review and its initial scan, filter, and metadata state."""
    media = db.get(MediaItemRow, media_item_id)
    if media is None:
        raise ReviewMembershipError("Media item is unavailable")
    version = media_source_id or ""
    requested_memberships = list(data.memberships)
    manual_scan, manual_filters = _append_manual_review_filters(
        db,
        media_item_id=media_item_id,
        media_source_id=version,
        values=data.new_manual_filters,
        owner_id=new_review_owner_columns(db)["owner_id"],
        stable_version_id=stable_version_id,
    )
    if manual_scan is not None:
        requested_memberships = _with_manual_membership(
            requested_memberships, manual_scan.id, manual_filters
        )
    membership_rows = root._validate_memberships(
        db,
        media_item_id=media_item_id,
        media_source_id=version,
        memberships=requested_memberships,
    )
    if duration_seconds is None:
        from .community_import_targets import review_duration

        known_durations = [
            duration
            for membership in membership_rows.values()
            if (duration := review_duration(membership.scan, media_item_id, version)) is not None
        ]
        if known_durations:
            duration_seconds = min(known_durations)
        else:
            duration_seconds = db.scalar(
                select(ScanRow.duration_seconds)
                .where(
                    ScanRow.media_item_id == media_item_id,
                    func.coalesce(ScanRow.media_source_id, "") == version,
                    ScanRow.duration_seconds.is_not(None),
                )
                .order_by(ScanRow.scan_number.desc())
                .limit(1)
            )
    review_document, document_from_import = _seed_review_mcf_document(
        media,
        requested_memberships,
        membership_rows,
        data.mcf_metadata_seed,
        catalog_mcf_document,
    )
    synchronization = _default_review_synchronization(duration_seconds)
    if not document_from_import:
        review_document = review_document.model_copy(update={"material_end_seconds": None})
    review_document = _document_for_synchronization(review_document, synchronization)
    row = SavedReviewRow(
        id=str(uuid.uuid4()),
        media_item_id=media_item_id,
        media_source_id=version,
        name=data.name,
        **new_review_owner_columns(db),
    )
    row.memberships = list(membership_rows.values())
    db.add(row)
    db.flush()
    if stable_version_id is not None:
        bind_review_version(db, row.id, stable_version_id)
    if any(value.source_id for value in data.community_memberships):
        raise ReviewMembershipError("A new review can only include community filters by link")
    plans = prepare_sources(db, row, data.community_memberships, data.community_drafts) or []
    row.community_sources = [plan.row for plan in plans]
    db.flush()
    row.mcf_document = _review_mcf_document_row(row.id, review_document)
    row.synchronization = _review_synchronization_row(row.id, synchronization)
    if data.filters is None:
        snapshot_memberships = membership_rows
        if manual_scan is not None and not any(
            value.scan_id == manual_scan.id for value in data.memberships
        ):
            snapshot_memberships = {
                scan_id: value
                for scan_id, value in membership_rows.items()
                if scan_id != manual_scan.id
            }
        inputs = [
            ReviewFilterInput(**root._review_filter(value).model_dump())
            for value in root._snapshot_findings(
                db, snapshot_memberships, media_item_id=media_item_id, media_source_id=version
            )
        ]
        snapshotted_provenance = {
            (value.source_scan_id, value.source_finding_id) for value in inputs
        }
        inputs.extend(
            value
            for value in manual_filters
            if (value.source_scan_id, value.source_finding_id) not in snapshotted_provenance
        )
    else:
        inputs = list(data.filters) + manual_filters
    inputs = root.fill_filter_defaults(inputs, base_filter_settings(db, row.owner_id)).filters
    stored_filters = review_filter_rows(db, row, membership_rows, inputs, plans)
    if data.filters is not None:
        from .review_filter_tracking import track_manual_edits

        track_manual_edits(db, row, stored_filters, synchronization, membership_rows)
    row.filters = stored_filters
    try:
        bind_review_evidence(db, row)
        prepare_review_policy(db, row)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    return root._review_detail(db, row, source=target_source, item_id=target_item_id)


def _append_manual_review_filters(
    db: Session,
    *,
    media_item_id: str,
    media_source_id: str,
    values: list[NewManualReviewFilter],
    owner_id: int | None = None,
    stable_version_id: str | None = None,
) -> tuple[ScanRow | None, list[ReviewFilterInput]]:
    """Add manually selected filters to a draft review without duplication."""
    if not values:
        return None, []
    values = root.fill_manual_filter_defaults(values, base_filter_settings(db, owner_id))
    manual_scan = manual_review_scan(
        db,
        media_item_id,
        media_source_id,
        {value.source for value in values},
        stable_version_id=stable_version_id,
    )
    inputs: list[ReviewFilterInput] = []
    for value in values:
        if (
            manual_scan.duration_seconds is not None
            and value.end_seconds > manual_scan.duration_seconds
        ):
            raise ValueError("Review filter range exceeds media duration")
        finding = ScanFindingRow(
            scan_id=manual_scan.id,
            manually_added=True,
            manually_edited=False,
            **value.model_dump(exclude={"id"}),
        )
        db.add(finding)
        db.flush()
        inputs.append(
            ReviewFilterInput(
                source_scan_id=manual_scan.id,
                source_finding_id=finding.id,
                **value.model_dump(),
            )
        )
    return manual_scan, inputs


def manual_review_scan(db, media_item_id, media_source_id, sources, *, stable_version_id=None):
    """Reuse or stage a target's manual scan without creating source findings."""
    media_item = db.get(MediaItemRow, media_item_id)
    if media_item is None:
        raise ReviewMembershipError("Media item is unavailable")
    source_path = media_item.path
    if stable_version_id is not None:
        version = get_version(db, stable_version_id)
        if not source_path or not version_file_matches(
            version, resolve_local_path(db, source_path).local_path, db=db
        ):
            raise ReviewMembershipError("The original Editarr review version is unavailable")
    query = (
        db.query(ScanRow)
        .outerjoin(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        .filter(
            ScanRow.media_item_id == media_item_id,
            ScanRow.media_source_id == (media_source_id or None),
            ScanRow.origin == ScanOrigin.MANUAL.value,
            ScanRow.status == "complete",
            ScanMediaVersionRow.version_id == stable_version_id,
        )
    )
    manual_scan = query.order_by(ScanRow.scan_number.desc()).first()
    if manual_scan is None:
        if not source_path:
            raise ReviewMembershipError("A Manual scan is required for new review filters")
        try:
            resolved_local_path = str(
                Path(resolve_local_path(db, source_path).local_path).resolve()
            )
        except ValueError as exc:
            raise ReviewMembershipError("The review media file is unavailable") from exc
        try:
            duration_seconds = root.get_video_duration(resolved_local_path)
        except (OSError, RuntimeError, ValueError):
            duration_seconds = None
        next_number = (
            db.query(func.max(ScanRow.scan_number))
            .filter(ScanRow.media_item_id == media_item_id)
            .scalar()
            or 0
        ) + 1
        manual_scan = ScanRow(
            id=str(uuid.uuid4()),
            media_item_id=media_item_id,
            scan_number=next_number,
            name="Manual",
            origin=ScanOrigin.MANUAL.value,
            source_path=source_path,
            media_source_id=media_source_id or None,
            media_source_name="Manual",
            resolved_local_path=resolved_local_path,
            video_hash=root._compute_video_hash(resolved_local_path),
            duration_seconds=duration_seconds,
            status="complete",
            scan_audio="audio" in sources,
            scan_video="visual" in sources,
            checkpoint_stage="manual",
            progress_percent=100.0,
            progress_stage="manual",
            progress_message="Manual filters",
            **new_scan_initiator_columns(db),
        )
        db.add(manual_scan)
        db.flush()
        if stable_version_id is not None:
            bind_scan_version(db, manual_scan.id, stable_version_id)
    manual_scan.scan_audio = manual_scan.scan_audio or "audio" in sources
    manual_scan.scan_video = manual_scan.scan_video or "visual" in sources
    return manual_scan


_AGGREGATE_FILTER_FIELDS = (
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
_EDL_FILTER_FIELDS = (
    "category",
    "subcategory",
    "label",
    "start_seconds",
    "end_seconds",
    "edit_action",
    "selected",
)
_MCF_FILTER_FIELDS = (
    "category",
    "subcategory",
    "severity",
    "mcf_channel",
    "mcf_comment",
    "start_seconds",
    "end_seconds",
    "selected",
)
_MCF_DOCUMENT_FIELDS = (
    "title",
    "year",
    "work_type",
    "season",
    "episode",
    "source_url",
    "imdb_url",
    "release",
    "comment",
    "material_start_seconds",
    "material_end_seconds",
)
