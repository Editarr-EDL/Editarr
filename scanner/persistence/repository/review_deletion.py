"""Caller-scoped preview and execution of saved-review deletion with file dispositions."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager, nullcontext

from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.persistence.rows.db_policies import ExportScope
from scanner.repository import ReviewRevisionConflict
from scanner.security.access.authorization import require
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide

from .export_discovery import AuthorizedPublication, export_media, require_export
from .export_recovery import publication_lock
from .review_access import DELETE, VIEW, ReviewAccess, not_found, review_facts
from .review_disposition import (
    EVERY_CHOICE,
    RETAIN_ONLY,
    Authorize,
    DeletionResult,
    DispositionConflict,
    FileChoice,
    execute_deletion,
    plan_deletion,
)
from .scan_jobs import scan_job_facts


class PublishedReviewDeletionBlocked(ValueError):
    def __init__(self):
        super().__init__(
            "Published files still reference this review; choose what happens to them first"
        )


def _export_media(db: Session, review_id: str):
    rows = db.query(MediaItemExportRow).filter(
        MediaItemExportRow.review_id == review_id,
        MediaItemExportRow.scope != ExportScope.LEGACY,
        MediaItemExportRow.snapshot_json.is_not(None),
    )
    return list(dict.fromkeys(export_media(row) for row in rows))


def review_media(db: Session, review_id: str, facts, scans) -> list:
    """Every media item whose evidence a deletion of the review needs, in a stable order."""
    return list(
        dict.fromkeys(
            [facts.media, *(item.media for item in scans.values()), *_export_media(db, review_id)]
        )
    )


def require_review_delete(caller: ReviewAccess, facts, evidence) -> None:
    """Refuse a review the caller may not see (404) or delete (403) with this evidence."""
    target = caller.ref(facts, evidence=evidence)
    if not isinstance(decide(caller.access, VIEW, target), Allowed):
        raise not_found()
    require(caller.access, DELETE, target)


def review_file_authority(
    caller: ReviewAccess, review_id: str, facts, scans, evidence: dict
) -> Authorize:
    """Recheck the review under the lock and classify its publications with `evidence`.

    Call while holding the writer lock that captured `evidence` for `review_media`.
    """
    db = caller.db
    if review_facts(db, review_id) != facts:
        raise not_found()
    require_review_delete(caller, facts, evidence[facts.media])
    if scan_job_facts(db, facts.memberships) != scans:
        raise not_found()
    for item in scans.values():
        caller.require_scan(item, caller.media_decision(item.media, evidence=evidence[item.media]))

    def authorize(row: MediaItemExportRow):
        item = export_media(row)
        if item not in evidence:
            raise DispositionConflict(
                "stale_review_deletion", "Published files changed; reload the deletion"
            )
        allowed = caller.media_decision(item, evidence=evidence[item])
        try:
            require_export(caller, row, allowed)
        except AccessDenied:
            return None
        try:
            require_export(caller, row, allowed, deleting=True)
        except AccessDenied:
            return RETAIN_ONLY
        return EVERY_CHOICE

    return authorize


@contextmanager
def _deletion_gate(caller: ReviewAccess, review_id: str) -> Iterator[Authorize]:
    """Hold the writer lock after the checks `ReviewAccess.run` applies to DELETE.

    Media evidence for the review, its scans, and its publications is gathered before
    the lock. The yielded callback classifies one publication with that evidence.
    """
    db = caller.db
    facts = caller.visible(review_id)
    scans = scan_job_facts(db, facts.memberships)
    if set(scans) != set(facts.memberships):
        raise not_found()
    media = review_media(db, review_id, facts, scans)
    with caller.writing(
        media, lambda captured: require_review_delete(caller, facts, captured[0])
    ) as captured:
        yield review_file_authority(
            caller, review_id, facts, scans, dict(zip(media, captured, strict=True))
        )


def preview_review_deletion(caller: ReviewAccess, review_id: str) -> dict:
    """Describe what deleting the review does to every file the caller may see."""
    db = caller.db
    with _deletion_gate(caller, review_id) as authorize:
        review = db.get(SavedReviewRow, review_id)
        plan = plan_deletion(db, review, authorize)
        return {
            "review_id": plan.review_id,
            "revision": plan.revision,
            "confirmation": plan.confirmation,
            "files": [
                {
                    "publication": AuthorizedPublication.capture(
                        caller, db.get(MediaItemExportRow, file.row_id)
                    ).publication,
                    "choices": sorted(file.choices),
                }
                for file in plan.files
            ],
            "legacy_formats": sorted(
                db.get(MediaItemExportRow, row_id).format.value for row_id in plan.legacy
            ),
            "scan_ids": sorted(review_facts(db, review_id).memberships),
        }


def delete_review(
    caller: ReviewAccess,
    review_id: str,
    revision: int,
    *,
    confirmation: str | None = None,
    choices: Sequence[tuple[int, FileChoice]] = (),
) -> DeletionResult:
    """Delete a review, applying confirmed `(export_id, choice)` pairs.

    Choices are checked only after the review's VIEW and DELETE gate. Without a
    confirmation, only a review with no file the caller can see is deleted; hidden
    publications are then retained without disclosure.
    """
    explicit = confirmation is not None
    with publication_lock(caller.db) if explicit else nullcontext():
        with _deletion_gate(caller, review_id) as authorize:
            review = caller.db.get(SavedReviewRow, review_id)
            if review.revision != revision:
                raise ReviewRevisionConflict(
                    "This review was updated elsewhere. Reload it before saving."
                )
            plan = plan_deletion(caller.db, review, authorize)
            if not explicit and (plan.files or plan.legacy):
                raise PublishedReviewDeletionBlocked()
            if explicit and confirmation != plan.confirmation:
                raise DispositionConflict(
                    "stale_review_deletion", "Published files changed; reload the deletion"
                )
            chosen = dict(choices)
            if len(chosen) != len(choices):
                raise DispositionConflict(
                    "review_deletion_choice_required", "List each published file once"
                )
            return execute_deletion(caller.db, plan, chosen, caller.access)
