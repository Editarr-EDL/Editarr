"""Caller-scoped discovery of retained canonical publications."""

import json
from dataclasses import dataclass, replace

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session, object_session

from scanner.db import MediaItemExportRow, MediaItemRow, UserRow
from scanner.modeling.model_publications import ExportPublication
from scanner.models import ExportFormat, ExportSummary
from scanner.persistence.rows.db_policies import ExportScope
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.identity import UserId
from scanner.security.access.records import MediaAllowed
from scanner.security.access.targets import AccountRef, ActionPair, MediaRef, ResourceRef
from scanner.security.access.vocabulary import Role

from .export_publication import publication_summary
from .review_access import media_facts, not_found
from .scan_jobs import ScanMedia


@dataclass(frozen=True)
class AuthorizedPublication:
    """Immutable projected publication and compatibility values from one read snapshot."""

    publication_json: str
    summary_json: str

    @property
    def publication(self) -> ExportPublication:
        return ExportPublication.model_validate_json(self.publication_json)

    @property
    def summary(self) -> ExportSummary:
        return ExportSummary.model_validate_json(self.summary_json)

    @classmethod
    def capture(cls, caller, row):
        """Materialize both views with direct status's source-review visibility projection."""
        from .repository_exports import _export_summary

        publication = caller.present(publication_status(row, caller.access.principal.user_id))
        summary = caller.present(_export_summary(row, row.format, row.review))
        summary = summary.model_copy(
            update={
                "present": row.content_bytes is not None,
                "current": publication["current"],
                "outdated": publication["outdated"],
            }
        )
        return cls(
            ExportPublication.model_validate(publication).model_dump_json(),
            summary.model_dump_json(),
        )


def export_media(row):
    snapshot = json.loads(row.snapshot_json)
    return ScanMedia(
        row.media_item.server_id,
        row.media_item.jellyfin_id,
        row.media_item.library_id,
        snapshot["media_path"],
    )


def require_export(caller, row, allowed, *, deleting=False):
    """Use the same media, account, and capability scope for lists and direct status."""
    if row.scope == ExportScope.DEFAULT:
        capability = C.EXPORTS_DEFAULT_DELETE if deleting else C.EXPORTS_DEFAULT_PUBLISH
        require(caller.access, capability, MediaRef(allowed))
        return
    owner = caller.db.get(UserRow, row.target_user_id)
    if owner is None:
        raise not_found()
    action = ActionPair(
        C.EXPORTS_PERSONAL_DELETE_OWN if deleting else C.EXPORTS_PERSONAL_DOWNLOAD_OWN,
        C.EXPORTS_PERSONAL_DELETE_OTHER,
    )
    require(
        caller.access,
        action,
        ResourceRef(AccountRef(UserId(owner.id), Role(owner.role)), allowed),
    )


# Pending and durable withdrawals both read as failed; `serving` tells them apart.
# A deleted record has no work or failure, so it reads as idle and not serving.
PUBLIC_REGENERATION_STATES = {
    "queued": "queued",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "withdrawing": "failed",
    "withdrawn": "failed",
}


def publication_status(row, user_id):
    """Describe one destination without collapsing targets or exposing private snapshots."""
    review = row.review
    revision = (
        (
            review.edl_content_revision
            if row.format == ExportFormat.EDL
            else review.mcf_content_revision
        )
        if review is not None
        else None
    )
    snapshot = json.loads(row.snapshot_json)
    job = row.regeneration
    from .export_regeneration import policy_identity

    db = object_session(row)
    policy_current = db is not None and policy_identity(db, row) == (
        row.policy_owner_id,
        row.policy_revision,
    )
    state = "idle" if job is None or job.deleted else PUBLIC_REGENERATION_STATES[job.state]
    current = (
        row.content_bytes is not None
        and policy_current
        and state not in {"queued", "running", "failed"}
        and (review is not None or row.scope == ExportScope.PERSONAL)
        and (review is None or row.exported_format_revision == revision)
    )
    target = snapshot.get("target") or (
        "item_default"
        if row.scope == ExportScope.DEFAULT
        else "self"
        if row.target_user_id == row.actor_id
        else "review_owner"
    )
    return {
        **publication_summary(row),
        "media_item_id": row.media_item_id,
        "media_source_id": row.media_source_id,
        "target": target,
        "current": current,
        "outdated": not current,
        "regeneration_state": state,
        "regenerating": state in {"queued", "running"},
        "failed": state == "failed",
        "failure_reason": "write_failed" if state == "failed" else None,
        "retryable": state == "failed",
        "serving": row.content_bytes is not None,
        "desired_policy_revision": job.policy_revision if state != "idle" else None,
    }


def discover_publications(
    caller, media_item_id, *, media_source_id="", review_id=None
) -> tuple[AuthorizedPublication, ...]:
    """Return only canonical rows whose direct status the live caller can read."""
    with Session(bind=caller.db.get_bind()) as snapshot:
        snapshot.execute(text("BEGIN"))
        reader = replace(caller, db=snapshot)
        reader.reload()
        return _discover_publications(reader, media_item_id, media_source_id, review_id)


def _discover_publications(
    caller, media_item_id, media_source_id, review_id
) -> tuple[AuthorizedPublication, ...]:
    media_row = caller.db.get(MediaItemRow, media_item_id)
    if media_row is None:
        raise not_found()
    if review_id is not None:
        caller.visible(review_id)
    query = caller.db.query(MediaItemExportRow).filter(
        MediaItemExportRow.media_item_id == media_item_id,
        MediaItemExportRow.scope != ExportScope.LEGACY,
        MediaItemExportRow.snapshot_json.is_not(None),
    )
    if media_source_id is not None:
        query = query.filter(MediaItemExportRow.media_source_id == media_source_id)
    if review_id is not None:
        query = query.filter(MediaItemExportRow.review_id == review_id)
    if not isinstance(caller.media_decision(media_facts(caller.db, media_row)), MediaAllowed):
        raise not_found()
    result = []
    for row in query.order_by(MediaItemExportRow.id):
        allowed = caller.media_decision(export_media(row))
        try:
            require_export(caller, row, allowed)
        except AccessDenied:
            continue
        result.append(AuthorizedPublication.capture(caller, row))
    return tuple(result)


def with_publications(caller, status, publications):
    """Keep legacy status separate and project only the caller's personal format badges."""
    exports = dict(status.exports)
    records = []
    for discovered in publications:
        publication = discovered.publication
        records.append(publication)
        if publication.scope == "personal" and publication.target_user_id == int(
            caller.access.principal.user_id
        ):
            exports[publication.format] = discovered.summary
    return status.model_copy(
        update={
            "exports": exports,
            "legacy_exports": status.exports,
            "publications": records,
        }
    )


def project_publications(caller, value):
    """Attach authorized discovery to saved-review payloads at the HTTP boundary."""
    from scanner.models import SavedReviewSummary

    if isinstance(value, SavedReviewSummary):
        exists = (
            caller.db.query(MediaItemExportRow.id)
            .filter(
                MediaItemExportRow.review_id == value.id,
                MediaItemExportRow.scope != ExportScope.LEGACY,
                MediaItemExportRow.snapshot_json.is_not(None),
            )
            .first()
        )
        if exists is None:
            return value.model_copy(
                update={
                    "exports": with_publications(caller, value.exports, []),
                }
            )
        try:
            caller.visible(value.id)
        except AccessDenied:
            return value
        publications = discover_publications(
            caller,
            value.media_item_id,
            media_source_id=value.target.media_source_id or "",
            review_id=value.id,
        )
        return value.model_copy(
            update={
                "exports": with_publications(caller, value.exports, publications),
            }
        )
    if isinstance(value, BaseModel):
        return value.model_copy(
            update={key: project_publications(caller, item) for key, item in value.__dict__.items()}
        )
    if isinstance(value, list):
        return [project_publications(caller, item) for item in value]
    return value


def catalog_publication_status(
    caller, status, *, server_id, item_id, item_type, media_source_id=None, required=False
):
    """Decorate one already filtered catalog item or physical version with discovery."""
    media = (
        caller.db.query(MediaItemRow)
        .filter_by(
            server_id=server_id,
            jellyfin_id=item_id,
            item_type=item_type,
        )
        .one_or_none()
    )
    try:
        publications = (
            discover_publications(caller, media.id, media_source_id=media_source_id)
            if media is not None
            else []
        )
    except AccessDenied:
        if required:
            raise
        publications = []
    return with_publications(caller, status, publications)
