"""Locked personal and item-default export lifecycle and durable provenance."""

import errno
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from stat import S_ISREG
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import MediaItemExportRow, ScanFindingRow, UserProviderIdentityRow, UserRow
from scanner.formats.export_layout import (
    ExportId,
    LocalExportId,
    default_export_path,
    parse_jellyfin_user_id,
    personal_export_path,
)
from scanner.models import ExportFormat, ReviewExportResponse, ScanFinding
from scanner.persistence.archive import archived_reads, live_only
from scanner.persistence.rows.db_policies import ExportScope
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import UserId
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import stage_event

from .export_recovery import (
    deleted,
    digest,
    ensure_export_directory,
    owned_digest,
    recover_sidecar,
)
from .review_export_content import PreparedReviewExport, prepare_review_export
from .review_policy import policy_write

SNAPSHOT_VERSION = "editarr-export-snapshot-1"


class ExportConflict(ValueError):
    def __init__(self, code: str, message: str, *, confirmation: str | None = None):
        super().__init__(message)
        self.code, self.confirmation = code, confirmation


def resolve_directory(path: Path) -> Path:
    """Resolve an existing `path`, raising OSError when it cannot be resolved.

    Python 3.11 raises RuntimeError for a symbolic-link loop; export callers defer on
    OSError and treat only ENOENT as absence, so a loop is ELOOP here.
    """
    try:
        return path.resolve(strict=True)
    except RuntimeError as exc:
        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(path)) from exc


def safe_destination(
    media: Path, export_format: ExportFormat, target: ExportId | None, *, require_media=True
) -> Path:
    """Resolve a server-selected destination without following export symlinks.

    Call under the publication write lock before creating directories or files.
    The media parent is resolved once; every export component must stay within it.
    Disable require_media only to recover or withdraw a retained destination.
    """
    media = resolve_directory(media.parent) / media.name
    if media.is_symlink() or (require_media and not media.is_file()):
        raise ValueError("Media must be a regular file, not a symbolic link")
    destination = (
        personal_export_path(media, target, export_format)
        if target is not None
        else default_export_path(media, export_format)
    )
    check_export_components(media.parent, destination)
    if destination.exists() and not S_ISREG(destination.stat().st_mode):
        raise ValueError("Export destination must be a regular file")
    return destination


def check_export_components(media_directory: Path, destination: Path) -> None:
    """Refuse a symbolic link at any entry from `media_directory` down to `destination`.

    `media_directory` must already be resolved. Every parent that exists must be a
    directory. Raises `ExportConflict` for a link and ValueError for another entry.
    """
    current = media_directory
    for component in destination.relative_to(media_directory).parts:
        current = current / component
        if current.is_symlink():
            raise ExportConflict(
                "export_destination_changed", "Symbolic links are not valid export destinations"
            )
        if current.exists() and current != destination and not current.is_dir():
            raise ValueError("Export parent must be a directory")


def prepare_export_directory(
    media: Path,
    export_format: ExportFormat,
    target: ExportId | None,
    destination: Path,
    *,
    require_media=True,
) -> None:
    """Create and persist the destination's parents, then confirm it still resolves there.

    Call under the publication write lock after `safe_destination` returned `destination`.
    """
    try:
        ensure_export_directory(destination.parent, media_directory=media.parent)
    except ValueError as exc:
        raise ExportConflict("export_destination_changed", "Export destination changed") from exc
    if safe_destination(media, export_format, target, require_media=require_media) != destination:
        raise ExportConflict("export_destination_changed", "Export destination changed")


def export_identity(db: Session, user_id: int) -> ExportId:
    """Use a persisted provider GUID or the account's immutable local namespace."""
    user = db.get(UserRow, user_id)
    if user is None or user.status != "active":
        raise ExportConflict("export_target_unavailable", "Export target is unavailable")
    identity = db.get(UserProviderIdentityRow, user_id)
    if identity is None:
        return LocalExportId(user.local_export_id)
    if identity.link_state not in {"verified", "outage_grace"}:
        raise ExportConflict("export_target_unavailable", "Export target must be verified")
    return parse_jellyfin_user_id(identity.provider_user_id)


def publication_summary(row: MediaItemExportRow) -> dict:
    """Return identifiers and revisions, never filesystem paths or private content."""
    return {
        "id": row.id,
        "format": row.format.value,
        "scope": row.scope.value,
        "target_user_id": row.target_user_id,
        "target_export_id": row.target_export_id,
        "actor_id": row.actor_id,
        "policy_owner_id": row.policy_owner_id,
        "original_author_id": row.original_author_id,
        "policy_revision": row.policy_revision,
        "review_id": row.review_id,
        "exported_format_revision": row.exported_format_revision,
        "publication_revision": row.publication_revision,
        "publication_id": (
            json.loads(row.snapshot_json)["publication_id"] if row.snapshot_json else None
        ),
        "content_sha256": digest(row.content_bytes),
    }


def _event(db, row, access, action, *, review_revision=None):
    details = {
        "format": row.format.value,
        "target_kind": "personal" if row.scope == ExportScope.PERSONAL else "item_default",
        "policy_revision": row.policy_revision,
        "source_owner_id": row.policy_owner_id,
    }
    if review_revision is not None:
        details["review_revision"] = review_revision
    stage_event(
        db,
        AuditEvent(
            occurred_at=datetime.now(UTC),
            actor=access.principal.user_id,
            credential=access.principal.credential,
            action=action,
            outcome=AuditOutcome.SUCCEEDED,
            target_user=UserId(row.target_user_id) if row.target_user_id is not None else None,
            media_key=row.media_item_id,
            resource=f"export:{row.id}",
            details=details,
        ),
    )


@dataclass(frozen=True)
class Publication:
    row: MediaItemExportRow | None
    scope: ExportScope
    target_user_id: int | None
    target: ExportId | None
    destination: Path
    output: PreparedReviewExport
    access: PrincipalAccess
    target_choice: str

    def stage(self, db: Session, row: MediaItemExportRow, content: bytes) -> None:
        """Stage exact retained content and its audit event in the publication transaction."""
        review = self.output.review
        row.actor_id = int(self.access.principal.user_id)
        row.target_user_id = self.target_user_id
        row.target_export_id = self.target.value if self.target is not None else None
        row.content_bytes = content
        row.destination_path = str(self.destination)
        row.publication_revision = (row.publication_revision or 0) + 1
        row.intended_policy_owner_id = None
        row.regeneration = None
        row.snapshot_json = json.dumps(
            {
                "version": SNAPSHOT_VERSION,
                "publication_id": str(uuid4()),
                **retained_review_snapshot(db, self.output),
                "actor_id": row.actor_id,
                "policy_owner_id": review.owner_id,
                "original_author_id": review.original_author_id,
                "policy_revision": self.output.policy.policy_revision,
                "target_user_id": self.target_user_id,
                "target_export_id": row.target_export_id,
                "target": self.target_choice,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        db.flush()
        _event(db, row, self.access, AuditAction.EXPORT_PUBLISH, review_revision=review.revision)


def retained_review_snapshot(db, output):
    """Capture the same immutable evidence for publication and successful regeneration."""
    from scanner.repository import get_saved_review

    from .media_identity_store import review_version_id

    review = output.review
    snapshot = {
        "source_review_id": review.id,
        "review_revision": review.revision,
        "media_path": str(output.media),
        "review": get_saved_review(db, review.id).model_dump(mode="json"),
        "policy_evidence": [
            {column.name: getattr(binding, column.name) for column in binding.__table__.columns}
            for binding in review.policy_evidence
        ],
        "sources": [
            {
                "scan_id": membership.scan_id,
                "role": membership.role,
                "findings": [
                    ScanFinding.model_validate(finding, from_attributes=True).model_dump(
                        mode="json"
                    )
                    for finding in db.query(ScanFindingRow)
                    .filter_by(scan_id=membership.scan_id)
                    .order_by(ScanFindingRow.id)
                ],
            }
            for membership in output.memberships
        ],
    }
    version_id = review_version_id(db, review.id)
    if version_id is not None:
        snapshot["media_version_id"] = version_id
    return snapshot


def _current(db, output, scope, target_user_id):
    return (
        db.query(MediaItemExportRow)
        .filter(
            MediaItemExportRow.media_item_id == output.review.media_item_id,
            MediaItemExportRow.media_source_id == output.review.media_source_id,
            MediaItemExportRow.format == output.format,
            MediaItemExportRow.scope == scope,
            MediaItemExportRow.target_user_id == target_user_id,
        )
        .one_or_none()
    )


def _released(db, output, export_id):
    """A deleted account's retained publication at the export ID a new account now holds.

    Account deletion releases a Jellyfin identity (MU-38), so a re-admitted user
    can receive the same export ID. Its publication then replaces the retained one
    only after the replacement confirmation, like any publication by another review.
    """
    from scanner.persistence.rows.db_account_dispositions import AccountDispositionRow

    return (
        db.query(MediaItemExportRow)
        .join(
            AccountDispositionRow,
            AccountDispositionRow.user_id == MediaItemExportRow.target_user_id,
        )
        .filter(
            MediaItemExportRow.media_item_id == output.review.media_item_id,
            MediaItemExportRow.media_source_id == output.review.media_source_id,
            MediaItemExportRow.format == output.format,
            MediaItemExportRow.scope == ExportScope.PERSONAL,
            MediaItemExportRow.target_export_id == export_id.value,
        )
        .order_by(MediaItemExportRow.id)
        .first()
    )


def filename_collision(db, destination, excluding: int | None) -> ExportConflict:
    """The refusal for `destination` that a publication other than `excluding` records."""
    table = MediaItemExportRow.__table__
    holders = select(table.c.id).where(
        table.c.destination_path == str(destination),
        table.c.id != (excluding if excluding is not None else -1),
        ~live_only(MediaItemExportRow),
    )
    archived = db.execute(holders.limit(1)).first() is not None
    message = (
        "An archived record holds this filename"
        if archived
        else "Another media version owns this filename"
    )
    return ExportConflict("export_filename_collision", message)


def _check_destination(db, row, destination, *, replacing=False):
    """Refuse another owner or unrecognized bytes at `destination` for `row`.

    With `replacing`, return deleted records of other versions at this path; their owned
    bytes are recognized and the caller removes the records with its publication. An
    archived record keeps its path, since the archive keeps every row.
    """
    with archived_reads(db):
        others = (
            db.query(MediaItemExportRow)
            .filter(
                MediaItemExportRow.destination_path == str(destination),
                MediaItemExportRow.id != (row.id if row is not None else -1),
            )
            .all()
        )
        replaceable = all(
            deleted(other) and other.media_item.archived_source is None for other in others
        )
    if others and not (replacing and replaceable):
        raise filename_collision(db, destination, row.id if row is not None else None)
    if row is not None and row.destination_path != str(destination):
        raise ExportConflict(
            "export_destination_changed", "Export destination changed; resolve it first"
        )
    owned = {owned_digest(owner) for owner in (row, *others) if owner is not None} - {None}
    recover_sidecar(destination, row.content_bytes if row is not None else None, owned=owned)
    if destination.exists() and digest(destination.read_bytes()) not in owned:
        raise ExportConflict(
            "export_file_collision", "An unrecognized file occupies the export destination"
        )
    return others


@policy_write
def publish_export(
    db: Session,
    review_id: str,
    revision: int,
    export_format: ExportFormat,
    *,
    access: PrincipalAccess,
    target: str = "self",
    confirmation: str | None = None,
    policy_revision: int | None = None,
    policy_owner_id: int | None = None,
) -> dict | None:
    """Publish one authorized review after deciding destination and confirmation while locked."""
    from .export_discovery import publication_status
    from .repository_exports import _export_summary, _review_summary
    from .repository_review_exports import publish_review_sidecar
    from .repository_rows import _utc

    if not db.info.get("export_publication_lock"):
        raise ValueError("Acquire publication_lock before the authorized writer transaction")
    output = prepare_review_export(
        db,
        review_id,
        revision,
        export_format,
        policy_revision=policy_revision,
        policy_owner_id=policy_owner_id,
    )
    if output is None:
        return None
    if target not in {"self", "review_owner", "item_default"}:
        raise ValueError("Invalid export target")
    personal = target != "item_default"
    target_user_id = (
        (int(access.principal.user_id) if target == "self" else output.review.owner_id)
        if personal
        else None
    )
    export_id = export_identity(db, target_user_id) if personal else None
    scope = ExportScope.PERSONAL if personal else ExportScope.DEFAULT
    destination = safe_destination(output.media, export_format, export_id)
    row = _current(db, output, scope, target_user_id)
    if row is None and personal:
        row = _released(db, output, export_id)
    replaced = _check_destination(db, row, destination, replacing=True)
    live = None if row is None or deleted(row) else row
    state = {
        "review": review_id,
        "revision": revision,
        "policy_owner": output.policy.owner_id,
        "policy": output.policy.policy_revision,
        "destination": str(destination),
        "content": digest(output.content),
        "current": publication_summary(live) if live else None,
    }
    expected = digest(json.dumps(state, sort_keys=True, separators=(",", ":")).encode())
    if confirmation is not None and confirmation != expected:
        raise ExportConflict(
            "stale_export_confirmation", "The export changed; confirm its current state"
        )
    if live is not None:
        source_id = (
            json.loads(row.snapshot_json)["source_review_id"]
            if row.snapshot_json
            else row.review_id
        )
        if source_id != review_id and confirmation != expected:
            raise ExportConflict(
                "export_confirmation_required",
                "Confirm replacement by another review",
                confirmation=expected,
            )
    prepare_export_directory(output.media, export_format, export_id, destination)
    for other in replaced:
        db.delete(other)
    db.flush()
    publication = Publication(
        row, scope, target_user_id, export_id, destination, output, access, target
    )
    row = publish_review_sidecar(
        db,
        review=output.review,
        memberships=list(output.memberships),
        destination=destination,
        content=output.content,
        export_format=export_format,
        content_revision=output.format_revision,
        policy=output.policy,
        publication=publication,
    )
    status = publication_status(row, access.principal.user_id)
    summary = _export_summary(row, export_format, output.review)
    compatibility = ReviewExportResponse(
        format=export_format,
        review_id=review_id,
        exported_format_revision=row.exported_format_revision,
        exported_at=_utc(row.exported_at),
        exported_scans=summary.exported_scans,
        exported_review=_review_summary(db, output.review),
        policy_owner_id=row.policy_owner_id,
        policy_revision=row.policy_revision,
        publication=status,
    )
    return {**publication_summary(row), "publication": status, "compatibility": compatibility}
