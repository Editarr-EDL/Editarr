"""Explicit signed downloads, fresh service key history and original-byte verification."""

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scanner.formats.community.codec import ResourceBounds
from scanner.security.community_artifact_signatures import (
    HistoricalKey,
    VerifiedArtifact,
    verify_artifact_signature,
)
from scanner.security.community_download_receipts import (
    DownloadReceipt,
    ReceiptKey,
    parse_receipt_keys,
    public_bytes,
    verify_download_receipt,
)
from scanner.security.community_identity import InstallationIdentity

from .transport import CommunityFailure, CommunityTransport


class AuthorKey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    contributorId: str = Field(max_length=128)
    keyId: str = Field(max_length=128)
    publicKey: str = Field(max_length=44)
    status: Literal["active", "retired", "revoked", "compromised"]


class DownloadBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    revisionId: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    artifactSchema: Literal["community-artifact-1", "community-artifact-2"]
    artifactFormat: Literal["gzip-json"]
    artifactDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifestDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    envelopeDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact: str
    manifest: str
    envelope: str
    historicalKey: AuthorKey
    receipt: dict


@dataclass(frozen=True)
class VerifiedDownload:
    service: str
    contribution_id: str
    revision_id: str
    verified: VerifiedArtifact
    receipt: DownloadReceipt
    trusted_receipt_keys: tuple[ReceiptKey, ...]


def _part(value: str, digest: str, limit: int) -> bytes:
    if len(value) > 4 * ((limit + 2) // 3):
        raise CommunityFailure("download_limit")
    try:
        original = base64.b64decode(value, validate=True)
        if len(original) > limit or base64.b64encode(original).decode("ascii") != value:
            raise ValueError
        if hashlib.sha256(original).hexdigest() != digest:
            raise ValueError
        return original
    except (ValueError, UnicodeError) as exc:
        raise CommunityFailure("invalid_artifact") from exc


def verify_download_bundle(
    value: object,
    *,
    service: str,
    contribution_id: str,
    revision_id: str,
    contributor_id: str,
    trusted_keys: tuple[ReceiptKey, ...],
    bounds: ResourceBounds,
) -> VerifiedDownload:
    """Verify a service response; contribution identity comes from its signed catalog result."""
    try:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", contribution_id):
            raise ValueError
        if not isinstance(value, dict) or type(value.get("version")) is not int:
            raise ValueError
        bundle = DownloadBundle.model_validate(value, strict=True)
        if bundle.revisionId != revision_id:
            raise ValueError
        original = _part(bundle.artifact, bundle.artifactDigest, bounds.compressed_bytes)
        manifest = _part(bundle.manifest, bundle.manifestDigest, bounds.expanded_bytes)
        envelope = _part(bundle.envelope, bundle.envelopeDigest, 256)
        key = bundle.historicalKey
        verified = verify_artifact_signature(
            artifact_bytes=original,
            manifest_bytes=manifest,
            envelope_bytes=envelope,
            historical_key=HistoricalKey(
                key.contributorId, key.keyId, public_bytes(key.publicKey, 32), key.status
            ),
            expected_submission_id=revision_id,
            bounds=bounds,
        )
        if bundle.artifactSchema != verified.artifact.artifact.schema_version:
            raise ValueError
        receipt = verify_download_receipt(
            bundle.receipt,
            service=service,
            contributor_id=contributor_id,
            revision_id=revision_id,
            artifact_digest=bundle.artifactDigest,
            trusted_keys=trusted_keys,
        )
        return VerifiedDownload(
            service, contribution_id, revision_id, verified, receipt, trusted_keys
        )
    except ValueError as exc:
        raise CommunityFailure("invalid_artifact") from exc


async def fetch_verified_download(
    transport: CommunityTransport,
    identity: InstallationIdentity,
    *,
    contribution_id: str,
    revision_id: str,
    bounds: ResourceBounds,
    response_bytes: int,
) -> VerifiedDownload:
    """Refresh service-origin key status before each explicit download; never use stale fallback."""
    if any(
        not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value)
        for value in (revision_id, contribution_id)
    ):
        raise CommunityFailure("invalid_revision")
    if not identity.contributor_id or identity.service_url != transport.endpoint.url:
        raise CommunityFailure("wrong_service")
    history = await transport.request(
        "GET",
        "/v1/receipt-keys",
        identity=identity,
        verify_response_digest=True,
    )
    try:
        if set(history) != {"version", "service", "keys"} or type(history["version"]) is not int:
            raise ValueError
        if history["version"] != 1 or history["service"] != transport.endpoint.url:
            raise ValueError
        keys = parse_receipt_keys(history["keys"])
    except ValueError as exc:
        raise CommunityFailure("invalid_receipt_keys") from exc
    value = await transport.request(
        "GET",
        f"/v1/contributions/{revision_id}/artifact",
        identity=identity,
        response_bytes=response_bytes,
        verify_response_digest=True,
    )
    return verify_download_bundle(
        value,
        service=transport.endpoint.url,
        contribution_id=contribution_id,
        revision_id=revision_id,
        contributor_id=identity.contributor_id,
        trusted_keys=keys,
        bounds=bounds,
    )


async def download_and_import(
    db,
    transport: CommunityTransport,
    identity,
    target,
    *,
    credential_ref: str,
    contribution_id: str,
    revision_id: str,
    bounds: ResourceBounds,
    response_bytes: int,
    confirm_uncertain: bool = False,
):
    """Backend-only confirmed import; fetch fresh trust and verify all bytes before persistence."""
    from scanner.persistence.repository.community_imports import import_verified_download

    if db.new or db.dirty or db.deleted:
        raise CommunityFailure("import_session_not_clean")
    downloaded = await fetch_verified_download(
        transport,
        identity,
        contribution_id=contribution_id,
        revision_id=revision_id,
        bounds=bounds,
        response_bytes=response_bytes,
    )
    return import_verified_download(
        db,
        downloaded,
        target,
        credential_ref=credential_ref,
        bounds=bounds,
        confirm_uncertain=confirm_uncertain,
    )
