"""Stage immutable approved bytes beside their durable publication operation."""

import hashlib

from sqlalchemy.orm import Session

from scanner.db import CommunityPublicationRow
from scanner.security.community_artifact_signatures import SignedArtifact

from .repository_community import enqueue_operation


def stage_publication(
    db: Session,
    *,
    signed: SignedArtifact,
    preview_digest: str,
    consent_token: str,
    author_key_id: str,
    source_kind: str = "scan",
    source_id: str | None = None,
    **intent,
) -> CommunityPublicationRow:
    """Flush both records; the caller must commit before any network work."""
    source_id = source_id or intent.get("scan_id")
    if source_kind not in {"scan", "review"} or not source_id:
        raise ValueError("Publication requires an explicit persisted source")
    operation = enqueue_operation(
        db,
        kind="publish",
        authored_review_id=source_id if source_kind == "review" else None,
        **intent,
    )
    values = dict(
        artifact_bytes=signed.artifact_bytes,
        manifest_bytes=signed.manifest_bytes,
        signature_bytes=signed.envelope_bytes,
        manifest_digest=hashlib.sha256(signed.manifest_bytes).hexdigest(),
        signature_digest=hashlib.sha256(signed.envelope_bytes).hexdigest(),
        preview_digest=preview_digest,
        consent_token=consent_token,
        author_key_id=author_key_id,
        source_kind=source_kind,
        source_id=source_id,
    )
    if operation.digest != hashlib.sha256(signed.artifact_bytes).hexdigest():
        raise ValueError("Publication digest does not match approved bytes")
    row = db.get(CommunityPublicationRow, operation.operation_id)
    if row is not None:
        if any(getattr(row, name) != value for name, value in values.items()):
            raise ValueError("Publication operation already has different approved bytes")
        return row
    row = CommunityPublicationRow(operation_id=operation.operation_id, **values)
    db.add(row)
    db.flush()
    return row
