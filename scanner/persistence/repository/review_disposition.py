"""Delete a saved review while retaining or removing each of its published files.

The plan is the unit of consent: preview and execution compute it from the same
locked state, and execution refuses unless the caller confirms the digest it saw.
Account deletion reuses `plan_deletion` and `stage_deletion` with its own
authorization callback and commits every review in one transaction.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.persistence.rows.db_policies import ExportScope
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.grants import PrincipalAccess
from scanner.security.audit.actions import AuditAction

from .export_deletion import removal_journal, stage_removal
from .export_publication import _event
from .export_recovery import deleted
from .repository_review_mutation import remove_review_row
from .review_policy import owner_policy


class FileChoice(StrEnum):
    RETAIN = "retain"
    REMOVE = "remove"


RETAIN_ONLY = frozenset({FileChoice.RETAIN})
EVERY_CHOICE = frozenset(FileChoice)


@dataclass(frozen=True)
class PublishedFile:
    """A canonical publication of the review that the deleting caller may see."""

    row_id: int
    publication_id: str
    publication_revision: int
    choices: frozenset[FileChoice]


@dataclass(frozen=True)
class DeletionPlan:
    """Everything a review deletion changes, with hidden rows kept out of `confirmation`.

    Legacy adjacent sidecars and hidden publications are always retained. Hidden rows
    never reach a caller-facing projection. Deleted records have no file to choose
    for; they are detached and keep their ownership evidence.
    """

    review_id: str
    revision: int
    files: tuple[PublishedFile, ...]
    legacy: tuple[int, ...]
    hidden: tuple[int, ...]
    deleted: tuple[int, ...]
    confirmation: str


@dataclass(frozen=True)
class DeletionResult:
    retained: tuple[int, ...]
    removed: tuple[int, ...]


class DispositionConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# Returns the permitted choices for a visible publication, or None when it is hidden.
Authorize = Callable[[MediaItemExportRow], frozenset[FileChoice] | None]


def _legacy(row: MediaItemExportRow) -> bool:
    return row.scope == ExportScope.LEGACY or row.snapshot_json is None


def plan_deletion(db: Session, review: SavedReviewRow, authorize: Authorize) -> DeletionPlan:
    """Classify every export row of `review` under the caller's current authority."""
    files, legacy, hidden, deleted_records, visible_legacy = [], [], [], [], []
    rows = (
        db.query(MediaItemExportRow)
        .filter(MediaItemExportRow.review_id == review.id)
        .order_by(MediaItemExportRow.id)
    )
    for row in rows:
        if _legacy(row):
            legacy.append(row.id)
            visible_legacy.append([row.id, row.format.value, row.exported_format_revision])
            continue
        if deleted(row):
            deleted_records.append(row.id)
            continue
        choices = authorize(row)
        if choices is None:
            hidden.append(row.id)
            continue
        snapshot = json.loads(row.snapshot_json)
        files.append(
            PublishedFile(row.id, snapshot["publication_id"], row.publication_revision, choices)
        )
    state = {
        "review": review.id,
        "revision": review.revision,
        "owner": review.owner_id,
        "policy": owner_policy(db, review.owner_id).policy_revision,
        "files": [
            [file.row_id, file.publication_id, file.publication_revision, sorted(file.choices)]
            for file in files
        ],
        "legacy": visible_legacy,
    }
    confirmation = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DeletionPlan(
        review.id,
        review.revision,
        tuple(files),
        tuple(legacy),
        tuple(hidden),
        tuple(deleted_records),
        confirmation,
    )


def _detach(row: MediaItemExportRow) -> None:
    row.review_id = row.exported_format_revision = None


def _retain(db: Session, row: MediaItemExportRow, review: SavedReviewRow, access, revision):
    """Detach the row from the review; its snapshot and policy owner keep the provenance.

    A personal row stamped for an earlier owner (a transferred review whose
    regeneration has not finished) records the review's owner as its intended
    policy owner, so detaching never loses whose policy it follows.
    """
    if row.scope == ExportScope.PERSONAL and row.policy_owner_id != review.owner_id:
        row.intended_policy_owner_id = review.owner_id
    _detach(row)
    if not _legacy(row):
        _event(db, row, access, AuditAction.EXPORT_RETAIN, review_revision=revision)


def stage_deletion(
    db: Session,
    plan: DeletionPlan,
    choices: Mapping[int, FileChoice],
    access: PrincipalAccess,
    journals: ExitStack,
) -> DeletionResult:
    """Stage `plan` and its review's deletion without committing.

    Removal journals enter `journals`; the caller commits inside it, so a failure
    before the commit restores every removed file. `choices` must name exactly the
    plan's visible files with a permitted choice.
    """
    if set(choices) != {file.row_id for file in plan.files}:
        raise DispositionConflict(
            "review_deletion_choice_required", "Choose retain or remove for every published file"
        )
    for file in plan.files:
        if choices[file.row_id] not in file.choices:
            raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    review = db.get(SavedReviewRow, plan.review_id)
    removed = [file.row_id for file in plan.files if choices[file.row_id] == FileChoice.REMOVE]
    if removed and not db.info.get("export_publication_lock"):
        raise ValueError("Acquire publication_lock before the authorized writer transaction")
    kept = [file.row_id for file in plan.files if choices[file.row_id] == FileChoice.RETAIN]
    for row_id in removed:
        row = db.get(MediaItemExportRow, row_id)
        destination = stage_removal(db, row, access, review_revision=plan.revision)
        if destination is None:
            _detach(row)
        else:
            journals.enter_context(removal_journal(db, row, destination))
    for row_id in (*plan.legacy, *plan.hidden, *kept):
        _retain(db, db.get(MediaItemExportRow, row_id), review, access, plan.revision)
    for row_id in plan.deleted:
        _detach(db.get(MediaItemExportRow, row_id))
    db.flush()
    remove_review_row(db, review, plan.revision)
    return DeletionResult(tuple(sorted(kept)), tuple(sorted(removed)))


def execute_deletion(
    db: Session,
    plan: DeletionPlan,
    choices: Mapping[int, FileChoice],
    access: PrincipalAccess,
) -> DeletionResult:
    """Apply `plan` and delete its review in one commit, restoring removed files on failure.

    Call inside the writer transaction that computed `plan`, under `publication_lock`
    when any file is removed. A crash leaves recovery journals that the next writer at
    each path reconciles against the committed rows. The result names visible files only.
    """
    with ExitStack() as journals:
        result = stage_deletion(db, plan, choices, access, journals)
        try:
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return result
