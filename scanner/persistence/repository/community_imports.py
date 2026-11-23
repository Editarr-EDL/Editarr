"""Atomic verified community imports and separately recoverable receipt staging."""

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scanner import repository as root
from scanner.db import (
    CommunityImportRow,
    CommunityImportTargetRow,
    CommunityOperationRow,
    MediaItemRow,
    ScanMediaItemAssociationRow,
    ScanRow,
)
from scanner.formats.community.codec import ResourceBounds
from scanner.integrations.community.downloads import VerifiedDownload
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_matching import Compatibility
from scanner.models import Scan
from scanner.runtime.scan_version import ScanVersionChanged, file_generation
from scanner.security.community_artifact_signatures import verify_artifact_signature
from scanner.security.community_download_receipts import DownloadReceipt, verify_download_receipt
from scanner.server_paths import UnresolvablePath

from .community_import_projection import (
    LocalImportTarget,
    canonical,
    import_compatibility,
    project_execution,
    project_findings,
    public_provenance,
)
from .community_import_targets import pending_target_channels
from .import_transaction import rollback_on_import_error, stage_imported_scan
from .media_identity_store import observe_sampled_version, observe_title
from .repository_community import attach_import, enqueue_operation, identity_connection
from .repository_rows import _scan_from_row


@dataclass(frozen=True)
class CommunityImportResult:
    scan: Scan
    reused: bool
    media_item_id: str
    media_source_id: str | None
    compatibility: Compatibility
    review_required: bool
    receipt_state: str
    import_id: str
    verification_pending: bool = False


def queue_import_receipt(
    db: Session,
    import_id: str,
    *,
    credential_ref: str,
    receipt: DownloadReceipt | None = None,
    actor_id: int | None = None,
):
    """Stage the original receipt for an already committed import; sending belongs to CSS-51.

    `credential_ref` names the identity that downloaded it and so owns the receipt.
    """
    imported = db.get(CommunityImportRow, import_id)
    if imported is None or not imported.artifact_bytes or not imported.receipt_json:
        raise CommunityFailure("import_evidence_unavailable")
    connection = identity_connection(db, credential_ref, imported.service_url)
    if connection is None or not connection.contributor_id:
        raise CommunityFailure("wrong_contributor")
    existing = (
        db.query(CommunityOperationRow)
        .filter_by(
            kind="receipt",
            service_url=imported.service_url,
            contributor_id=connection.contributor_id,
            scan_id=imported.scan_id,
            revision_id=imported.revision_id,
            digest=imported.digest,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing
    if receipt is None:
        receipt = DownloadReceipt.model_validate_json(imported.receipt_json)
    if (
        connection.contributor_id != receipt.receipt.contributor_id
        or receipt.receipt.service != imported.service_url
        or receipt.receipt.revision_id != imported.revision_id
        or receipt.receipt.artifact_digest != imported.digest.removeprefix("sha256:")
    ):
        raise CommunityFailure("wrong_contributor")
    return enqueue_operation(
        db,
        credential_ref=credential_ref,
        operation_id="import-" + receipt.receipt.receipt_id,
        service_url=imported.service_url,
        kind="receipt",
        contributor_id=connection.contributor_id,
        installation_id=connection.installation_id,
        contribution_id=imported.contribution_id,
        revision_id=imported.revision_id,
        digest=imported.digest,
        scan_id=imported.scan_id,
        receipt=canonical(receipt.model_dump(by_alias=True)),
        actor_id=actor_id,
    )


def _stored_local_path(db, server_path: str) -> str | None:
    """The stored media's local file; None when this host cannot resolve it."""
    try:
        return str(Path(root.resolve_local_path(db, server_path).local_path).resolve())
    except UnresolvablePath:
        return None


def _target_choices(previous: dict, target: LocalImportTarget, scan: ScanRow) -> None:
    """Record candidate channels without granting an approval or erasing existing ones."""
    pending = set(previous.get("pending_channels", ()))
    prior = previous.get("selected_channels")
    available = {name for name in ("audio", "video") if getattr(scan, f"scan_{name}")}
    approved = set(prior) if prior is not None else available
    selected = set(target.selected_channels) if target.selected_channels is not None else available
    if target.stage_for_verification:
        pending |= selected - approved
    elif prior is not None:
        approved |= selected - pending
    if prior is not None:
        previous["selected_channels"] = sorted(approved)
    if pending:
        previous["pending_channels"] = sorted(pending)


def _stage_import(db, downloaded, target, review_required, credential_ref):
    artifact = downloaded.verified.artifact.artifact
    digest = downloaded.verified.manifest.manifest.artifact_digest
    connection = identity_connection(db, credential_ref, downloaded.service)
    if (
        connection is None
        or not connection.installation_id
        or (connection.contributor_id != downloaded.receipt.receipt.contributor_id)
    ):
        raise CommunityFailure("wrong_contributor")
    media = db.get(MediaItemRow, target.media_item_id)
    if media is None or not Path(target.resolved_local_path).is_absolute():
        raise CommunityFailure("invalid_target")
    local_path = str(Path(target.resolved_local_path).resolve())
    if media.path and _stored_local_path(db, media.path) != local_path:
        raise CommunityFailure("invalid_target")
    fingerprint = target.fingerprint
    if target.stage_for_verification:
        if fingerprint is None:
            raise CommunityFailure("invalid_target")
        try:
            same_file = fingerprint.path == Path(local_path) and (
                fingerprint.generation == file_generation(local_path)
            )
        except (OSError, RuntimeError, ScanVersionChanged) as exc:
            raise CommunityFailure("target_changed") from exc
        if not same_file:
            raise CommunityFailure("target_changed")
    imported = (
        db.query(CommunityImportRow)
        .filter(
            CommunityImportRow.service_url == downloaded.service,
            CommunityImportRow.revision_id == downloaded.revision_id,
            CommunityImportRow.digest.in_([digest, "sha256:" + digest]),
        )
        .one_or_none()
    )
    reused = imported is not None
    if imported is not None:
        if (imported.contribution_id, imported.author_id, imported.artifact_type) != (
            downloaded.contribution_id,
            artifact.author.contributor_id,
            artifact.kind,
        ):
            raise CommunityFailure("import_conflict")
        if (imported.artifact_bytes, imported.manifest_bytes, imported.signature_bytes) != (
            downloaded.verified.signed.artifact_bytes,
            downloaded.verified.signed.manifest_bytes,
            downloaded.verified.signed.envelope_bytes,
        ):
            raise CommunityFailure("import_evidence_conflict")
        scan = db.get(ScanRow, imported.scan_id)
    else:
        next_number = (
            db.query(func.max(ScanRow.scan_number)).filter_by(media_item_id=media.id).scalar() or 0
        ) + 1
        channels = {item.channel for item in [*artifact.findings, *artifact.coverage]}
        scan = stage_imported_scan(
            db,
            media_item=media,
            scan_number=next_number,
            resolved_local_path=local_path,
            media_source_id=target.media_source_id or None,
            source_name=downloaded.service,
            review_name=artifact.name or f"Community {downloaded.revision_id[:32]}",
            duration_seconds=artifact.media.duration_seconds,
            has_audio=not channels or bool(channels & {"audio", "both"}),
            has_video=not channels or bool(channels & {"video", "both"}),
            progress_message="Imported community evidence",
            imported_at=datetime.now(timezone.utc),
            video_hash=fingerprint.digest if fingerprint else None,
        )
        findings = project_findings(downloaded, scan.id)
        project_execution(downloaded, scan)
        db.add_all(findings)
        db.flush()
        imported = attach_import(
            db,
            scan_id=scan.id,
            service_url=downloaded.service,
            contribution_id=downloaded.contribution_id,
            revision_id=downloaded.revision_id,
            digest=digest,
            author_id=artifact.author.contributor_id,
            artifact_type=artifact.kind,
        )
        signed = downloaded.verified.signed
        imported.artifact_bytes = signed.artifact_bytes
        imported.manifest_bytes = signed.manifest_bytes
        imported.signature_bytes = signed.envelope_bytes
        key = downloaded.verified.historical_key
        imported.historical_key_json = canonical(
            {
                "contributor_id": key.contributor_id,
                "key_id": key.key_id,
                "public_key": key.public_key.hex(),
                "status": key.status,
                "receipt_keys": [
                    item.model_dump(by_alias=True) for item in downloaded.trusted_receipt_keys
                ],
            }
        )
        imported.provenance_json = public_provenance(downloaded)
        imported.event_ids_json = canonical(
            {str(row.id): item.event_id for row, item in zip(findings, artifact.findings)}
        )
        imported.receipt_json = canonical(downloaded.receipt.model_dump(by_alias=True))
        if target.stage_for_verification and fingerprint is not None:
            observe_title(db, media, target.evidence.media)
            observe_sampled_version(
                db,
                media=media,
                media_source_id=target.media_source_id,
                source_path=media.path or local_path,
                fingerprint=fingerprint,
                known_content_sha256=target.known_content_sha256,
                scan_id=scan.id,
            )
    target_key = (imported.id, media.id, target.media_source_id or "")
    association = db.get(CommunityImportTargetRow, target_key)
    evidence_value = asdict(target.evidence)
    if association is None:
        if target.stage_for_verification or target.selected_channels is not None:
            evidence_value["selected_channels"] = []
        _target_choices(evidence_value, target, scan)
        if target.lookup_target_key is not None:
            evidence_value["lookup_target_key"] = target.lookup_target_key
        db.add(
            CommunityImportTargetRow(
                import_id=imported.id,
                media_item_id=media.id,
                media_source_id=target.media_source_id or "",
                resolved_local_path=local_path,
                video_hash=fingerprint.digest
                if fingerprint
                else root._compute_video_hash(local_path),
                target_evidence_json=canonical(evidence_value),
                review_required=review_required,
            )
        )
    else:
        previous = json.loads(association.target_evidence_json)
        approved = previous.pop("selected_channels", None)
        pending_channels = previous.pop("pending_channels", None)
        previous_key = previous.pop("lookup_target_key", None)
        if previous_key is not None and previous_key != target.lookup_target_key:
            raise CommunityFailure("target_association_conflict")
        if association.resolved_local_path != local_path or canonical(previous) != canonical(
            asdict(target.evidence)
        ):
            raise CommunityFailure("target_association_conflict")
        if approved is not None:
            previous["selected_channels"] = approved
        if pending_channels is not None:
            previous["pending_channels"] = pending_channels
        _target_choices(previous, target, scan)
        if target.lookup_target_key is not None:
            previous["lookup_target_key"] = target.lookup_target_key
        association.target_evidence_json = canonical(previous)
        association.review_required = association.review_required or review_required
    if (
        media.id != scan.media_item_id
        and not db.query(ScanMediaItemAssociationRow)
        .filter_by(
            scan_id=scan.id,
            media_item_id=media.id,
        )
        .first()
    ):
        db.add(ScanMediaItemAssociationRow(scan_id=scan.id, media_item_id=media.id))
    db.flush()
    if (
        DownloadReceipt.model_validate_json(imported.receipt_json).receipt.contributor_id
        != downloaded.receipt.receipt.contributor_id
    ):
        queue_import_receipt(
            db, imported.id, credential_ref=credential_ref, receipt=downloaded.receipt
        )
    return imported, scan, reused


def stage_verified_download(
    db: Session,
    downloaded: VerifiedDownload,
    target: LocalImportTarget,
    *,
    credential_ref: str,
    bounds: ResourceBounds,
    confirm_uncertain: bool = False,
):
    """Verify and stage an import without committing the caller's transaction."""
    signed = downloaded.verified.signed
    verified = verify_artifact_signature(
        artifact_bytes=signed.artifact_bytes,
        manifest_bytes=signed.manifest_bytes,
        envelope_bytes=signed.envelope_bytes,
        historical_key=downloaded.verified.historical_key,
        expected_submission_id=downloaded.revision_id,
        bounds=bounds,
    )
    receipt = verify_download_receipt(
        downloaded.receipt.model_dump(by_alias=True),
        service=downloaded.service,
        contributor_id=downloaded.receipt.receipt.contributor_id,
        revision_id=downloaded.revision_id,
        artifact_digest=verified.manifest.manifest.artifact_digest,
        trusted_keys=downloaded.trusted_receipt_keys,
    )
    downloaded = replace(downloaded, verified=verified, receipt=receipt)
    compatibility, review_required = import_compatibility(downloaded, target, bounds)
    if review_required and confirm_uncertain is not True and not target.stage_for_verification:
        raise CommunityFailure("explicit_review_required")
    imported, scan, reused = _stage_import(
        db,
        downloaded,
        target,
        review_required,
        credential_ref,
    )
    return imported, scan, reused, compatibility, review_required


def import_verified_download(
    db: Session,
    downloaded: VerifiedDownload,
    target: LocalImportTarget,
    *,
    credential_ref: str,
    bounds: ResourceBounds,
    confirm_uncertain: bool = False,
) -> CommunityImportResult:
    """Verify and commit an import, then independently queue its original receipt."""
    if db.new or db.dirty or db.deleted:
        raise CommunityFailure("import_session_not_clean")
    for attempt in range(2):
        try:
            with rollback_on_import_error(db):
                imported, scan, reused, compatibility, review_required = stage_verified_download(
                    db,
                    downloaded,
                    target,
                    credential_ref=credential_ref,
                    bounds=bounds,
                    confirm_uncertain=confirm_uncertain,
                )
                import_id, scan_id = imported.id, scan.id
                db.commit()
            break
        except IntegrityError:
            if attempt:
                raise
    receipt_state = "unavailable"
    try:
        with rollback_on_import_error(db):
            operation = queue_import_receipt(db, import_id, credential_ref=credential_ref)
            receipt_state = operation.state
            db.commit()
    except Exception:
        receipt_state = "unavailable"
    scan = db.get(ScanRow, scan_id)
    association = db.get(
        CommunityImportTargetRow,
        (import_id, target.media_item_id, target.media_source_id or ""),
    )
    return CommunityImportResult(
        _scan_from_row(scan, finding_count=len(downloaded.verified.artifact.artifact.findings)),
        reused,
        target.media_item_id,
        target.media_source_id,
        compatibility,
        review_required,
        receipt_state,
        import_id,
        bool(
            pending_target_channels(association)
            & set(target.selected_channels or ("audio", "video"))
        ),
    )
