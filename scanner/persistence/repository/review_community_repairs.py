"""Restore preserved review rows against freshly verified community evidence."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import DateTime, select

from scanner.db import (
    CommunityOperationRow,
    MediaItemExportScanRow,
    ReviewAcceptedCreditsRow,
    ReviewFilterRow,
    ReviewPolicyEvidenceRow,
    ReviewScanMembershipRow,
    SavedReviewRow,
    ScanFindingRow,
)
from scanner.integrations.community.downloads import VerifiedDownload
from scanner.integrations.community.transport import CommunityFailure

from ..write_lock import write_lock
from .community_import_projection import LocalImportTarget
from .community_imports import queue_import_receipt, stage_verified_download
from .review_repairs import preserved_rows, repair_records


@dataclass(frozen=True)
class RepairDownload:
    record_id: int
    missing_scan_id: str
    preserved_json: str
    community_json: str
    downloaded: VerifiedDownload
    target: LocalImportTarget


_RESTORED_TABLES = (
    (ReviewScanMembershipRow, "scan_id", None),
    (ReviewFilterRow, "source_scan_id", "source_finding_id"),
    (ReviewAcceptedCreditsRow, "scan_id", "source_finding_id"),
    (ReviewPolicyEvidenceRow, None, "source_finding_id"),
    (MediaItemExportScanRow, "scan_id", None),
)
_CONTENT = (
    "source",
    "category",
    "subcategory",
    "severity",
    "mcf_channel",
    "mcf_comment",
    "start_seconds",
    "end_seconds",
    "confidence",
    "label",
    "details",
    "group_key",
    "edit_action",
)


def _finding_map(preserved, identity, imported, db):
    references = {
        value[column]
        for model, _scan_column, column in _RESTORED_TABLES
        if column
        for value in preserved.get(model.__tablename__, [])
        if value[column] is not None
    }
    new_findings = list(
        db.scalars(select(ScanFindingRow).where(ScanFindingRow.scan_id == imported.scan_id))
    )
    new_ids = {value.id for value in new_findings}
    old_events = json.loads(identity.get("event_ids_json") or "{}")
    new_events = json.loads(imported.event_ids_json or "{}")
    result = {}
    if old_events:
        for old_id in references:
            event = old_events.get(str(old_id))
            matches = [int(key) for key, value in new_events.items() if event and value == event]
            if len(matches) != 1 or matches[0] not in new_ids:
                raise CommunityFailure("mapping_mismatch")
            result[old_id] = matches[0]
    else:
        old_findings = {value["id"]: value for value in preserved.get("scan_findings", [])}
        for old_id in references:
            old = old_findings.get(old_id)
            matches = [
                value.id
                for value in new_findings
                if old is not None
                and all(old.get(field) == getattr(value, field) for field in _CONTENT)
            ]
            if len(matches) != 1:
                raise CommunityFailure("mapping_mismatch")
            result[old_id] = matches[0]
    if len(set(result.values())) != len(result):
        raise CommunityFailure("mapping_mismatch")
    return result


def _values(model, values):
    values = dict(values)
    for column in model.__table__.columns:
        value = values.get(column.name)
        if isinstance(column.type, DateTime) and isinstance(value, str):
            values[column.name] = datetime.fromisoformat(value)
    return values


def restore_community_review(
    db, review_id, downloads, *, bounds, credential_ref, confirm_uncertain=False
):
    """Commit imports, every remapped row, receipt bindings, and flag removal together."""
    from .repository_review_core import _review_detail

    with write_lock(db):
        try:
            review = db.get(SavedReviewRow, review_id)
            records = repair_records(db, review_id)
            if not records:
                return _review_detail(db, review)
            prepared = {value.record_id: value for value in downloads}
            if set(prepared) != {value.id for value in records}:
                raise CommunityFailure("review_changed", retryable=True)
            for record in records:
                attempt = prepared[record.id]
                if (record.missing_scan_id, record.preserved_json, record.community_json) != (
                    attempt.missing_scan_id,
                    attempt.preserved_json,
                    attempt.community_json,
                ):
                    raise CommunityFailure("review_changed", retryable=True)
                identity = json.loads(record.community_json)
                downloaded = attempt.downloaded
                digest = downloaded.verified.manifest.manifest.artifact_digest
                if (
                    identity["service_url"],
                    identity["contribution_id"],
                    identity["revision_id"],
                    identity["digest"].removeprefix("sha256:"),
                ) != (
                    downloaded.service,
                    downloaded.contribution_id,
                    downloaded.revision_id,
                    digest,
                ):
                    raise CommunityFailure("digest_mismatch")
                imported, scan, _reused, _compatibility, _required = stage_verified_download(
                    db,
                    downloaded,
                    attempt.target,
                    bounds=bounds,
                    credential_ref=credential_ref,
                    confirm_uncertain=confirm_uncertain,
                )
                preserved = preserved_rows(record)
                finding_ids = _finding_map(preserved, identity, imported, db)
                for model, scan_column, finding_column in _RESTORED_TABLES:
                    for original in preserved.get(model.__tablename__, []):
                        values = _values(model, original)
                        if scan_column:
                            values[scan_column] = scan.id
                        if finding_column and values[finding_column] is not None:
                            values[finding_column] = finding_ids[values[finding_column]]
                        db.add(model(**values))
                receipt = json.loads(identity.get("receipt_json") or "null")
                if receipt:
                    previous = db.get(
                        CommunityOperationRow, "import-" + receipt["receipt"]["receiptId"]
                    )
                    if previous is not None:
                        previous.scan_id = scan.id
                operation = queue_import_receipt(db, imported.id, credential_ref=credential_ref)
                operation.scan_id = scan.id
                db.delete(record)
            review.revision += 1
            review.updated_at = datetime.now(timezone.utc)
            db.flush()
            db.expire(review)
            result = _review_detail(db, review)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
