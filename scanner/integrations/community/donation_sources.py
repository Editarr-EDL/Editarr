"""Bounded, read-only captures of persisted scan and saved-review inputs."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityImportRow,
    MediaItemRow,
    ReviewFilterRow,
    ReviewScanMembershipRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanRow,
    ScanStageCoverageRow,
)
from scanner.formats.community.codec import ArtifactError, ResourceBounds
from scanner.persistence.archive import live_only
from scanner.persistence.repository.review_policy import (
    ReviewPolicyConflict,
    prepare_review_policy,
    require_policy_evidence,
)
from scanner.persistence.repository.review_repairs import require_repaired


def canonical(value: object) -> bytes:
    def scalar(item):
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, Enum):
            return item.value
        raise TypeError("Unsupported snapshot value")

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=scalar,
    ).encode("ascii")


@dataclass(frozen=True)
class SourceRef:
    kind: Literal["scan", "review"]
    identifier: str


@dataclass(frozen=True)
class CapturedSource:
    reference: SourceRef
    private_bytes: bytes

    @property
    def guard(self) -> str:
        return hashlib.sha256(self.private_bytes).hexdigest()

    def data(self) -> dict:
        return json.loads(self.private_bytes)


def capture_source(
    bind: Engine, reference: SourceRef, *, bounds: ResourceBounds, source_limit: int
) -> CapturedSource:
    """Capture one consistent persisted generation; never commit or flush application rows."""
    if type(source_limit) is not int or source_limit <= 0:
        raise ArtifactError("An explicit positive source limit is required")
    if reference.kind not in {"scan", "review"} or not reference.identifier:
        raise ArtifactError("Invalid donation source")
    with Session(bind) as db:
        if bind.dialect.name == "sqlite":
            connection = db.connection()
            if connection.connection.driver_connection.in_transaction:
                raise ArtifactError("Snapshot capture requires an independent read connection")
            connection.exec_driver_sql("BEGIN")

        def rows(model, condition, limit, *order):
            if model in (MediaItemRow, ScanRow, SavedReviewRow):
                # Selecting the table bypasses the ORM archive guard.
                condition = condition & live_only(model)
            columns = [model.__table__]
            if model is CommunityImportRow:
                columns = [
                    column
                    for column in model.__table__.columns
                    if column.name
                    in {
                        "id",
                        "scan_id",
                        "service_url",
                        "contribution_id",
                        "revision_id",
                        "digest",
                        "author_id",
                        "artifact_type",
                        "created_at",
                        "event_ids_json",
                        "provenance_json",
                    }
                ]
            result = [
                dict(item)
                for item in db.execute(
                    select(*columns).where(condition).order_by(*order).limit(limit + 1)
                ).mappings()
            ]
            if len(result) > limit:
                raise ArtifactError("Donation source exceeds configured row bound")
            return result

        model = ScanRow if reference.kind == "scan" else SavedReviewRow
        root = rows(model, model.id == reference.identifier, 1)
        if not root:
            raise ArtifactError("Donation source is unavailable")
        root = root[0]
        if reference.kind == "review":
            require_repaired(db, reference.identifier)
        if reference.kind == "scan":
            memberships = []
            scan_ids = [root["id"]]
            findings = rows(
                ScanFindingRow,
                ScanFindingRow.scan_id == root["id"],
                bounds.events,
                ScanFindingRow.id,
            )
            synchronization = []
        else:
            memberships = rows(
                ReviewScanMembershipRow,
                ReviewScanMembershipRow.review_id == root["id"],
                source_limit,
                ReviewScanMembershipRow.position,
            )
            findings = rows(
                ReviewFilterRow,
                ReviewFilterRow.review_id == root["id"],
                bounds.events,
                ReviewFilterRow.position,
            )
            scan_ids = sorted(
                {item["scan_id"] for item in memberships}
                | {item["source_scan_id"] for item in findings}
            )
            synchronization = rows(
                ReviewSynchronizationRow, ReviewSynchronizationRow.review_id == root["id"], 1
            )
        if len(scan_ids) > source_limit:
            raise ArtifactError("Donation source lineage exceeds configured bound")
        scans = rows(ScanRow, ScanRow.id.in_(scan_ids), source_limit, ScanRow.id)
        if len(scans) != len(scan_ids):
            raise ArtifactError("Donation source lineage is unavailable")
        if any(item["status"] not in {"complete", "failed", "cancelled"} for item in scans):
            raise ArtifactError("Donation requires settled scan evidence")
        base_findings = findings
        policy = None
        required_ids = []
        evidence = []
        if reference.kind == "review":
            evidence = rows(
                ScanFindingRow,
                ScanFindingRow.scan_id.in_(scan_ids),
                bounds.events,
                ScanFindingRow.id,
            )
            try:
                prepared = prepare_review_policy(db, db.get(SavedReviewRow, reference.identifier))
                require_policy_evidence(prepared)
            except ReviewPolicyConflict as exc:
                raise ArtifactError("Review policy evidence is unavailable") from exc
            policy = {"owner_id": prepared.owner_id, "revision": prepared.policy_revision}
            required_ids = sorted(prepared.required_ids)
            effective = {value.id: value.model_dump(mode="json") for value in prepared.filters}
            findings = [{**value, **effective[value["id"]]} for value in findings]
        result = {
            "root": root,
            "scans": scans,
            "findings": findings,
            "base_findings": base_findings,
            "policy": policy,
            "policy_required": required_ids,
            "policy_evidence": evidence,
            "memberships": memberships,
            "synchronization": synchronization,
            "media": rows(MediaItemRow, MediaItemRow.id == root["media_item_id"], 1),
            "stages": rows(
                ScanStageCoverageRow,
                ScanStageCoverageRow.scan_id.in_(scan_ids),
                bounds.events,
                ScanStageCoverageRow.scan_id,
                ScanStageCoverageRow.stage,
                ScanStageCoverageRow.execution_id,
            ),
            "documents": rows(
                ScanMcfDocumentRow,
                ScanMcfDocumentRow.scan_id.in_(scan_ids),
                source_limit,
                ScanMcfDocumentRow.scan_id,
            ),
            "imports": rows(
                CommunityImportRow,
                CommunityImportRow.scan_id.in_(scan_ids),
                source_limit,
                CommunityImportRow.scan_id,
            ),
        }
        encoded = canonical(result)
        if len(encoded) > bounds.expanded_bytes:
            raise ArtifactError("Private source snapshot exceeds configured byte bound")
        return CapturedSource(reference, encoded)
