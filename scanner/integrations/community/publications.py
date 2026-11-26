"""Explicit submission outbox actions; never enroll or send during startup.

Approved `community-artifact-3` bytes are staged as a `submission` operation and sent
to `POST /v1/submissions` only on an explicit retry. Retired revision `publish`
operations remain readable but are never sent.
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone

from scanner.db import CommunityOperationRow, CommunityPublicationRow
from scanner.formats.community.artifact_v3 import ArtifactV3Error, decode_artifact_v3
from scanner.persistence.repository.community_publications import stage_publication
from scanner.persistence.repository.repository_community import (
    identity_connection,
    set_connection,
    set_operation_state,
)
from scanner.persistence.write_lock import write_lock
from scanner.security.audit.actions import AuditAction
from scanner.security.community_artifact_signatures import SignedArtifact
from scanner.security.community_artifact_v3_signatures import submission_request_body

from .client import CommunityClient
from .master_service import MasterService, encode
from .publication_status import REJECTED as _REJECTED
from .publication_status import publication_status
from .submissions import PreparedSubmission, approve_submission
from .transport import CommunityFailure

_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,128}")
KINDS = ("submission", "publish")


class PublicationOutbox:
    def __init__(self, client: CommunityClient, *, request_bytes: int | None):
        if request_bytes is not None and (
            type(request_bytes) is not int or not 0 < request_bytes <= 3_000_000
        ):
            raise ValueError("Explicit bounded publication request size required")
        self.client = client
        self.db = client.db
        self.request_bytes = request_bytes

    def _clean(self):
        if self.db.in_transaction():
            raise ValueError("Publication actions require a clean caller-owned Session")

    def _rows(self, operation_id):
        if not isinstance(operation_id, str) or not _TOKEN.fullmatch(operation_id):
            raise CommunityFailure("invalid_operation")
        operation = self.db.get(CommunityOperationRow, operation_id, populate_existing=True)
        payload = self.db.get(CommunityPublicationRow, operation_id, populate_existing=True)
        if operation is None or operation.kind not in KINDS or payload is None:
            raise CommunityFailure("publication_evidence_unavailable")
        return operation, payload

    def _body(self, operation, payload):
        if self.request_bytes is None:
            raise CommunityFailure("publication_not_configured")
        if (
            sum(
                len(value)
                for value in (
                    payload.artifact_bytes,
                    payload.manifest_bytes,
                    payload.signature_bytes,
                )
            )
            > self.request_bytes
        ):
            raise CommunityFailure("publication_limit")
        for contents, expected in (
            (payload.artifact_bytes, operation.digest),
            (payload.manifest_bytes, payload.manifest_digest),
            (payload.signature_bytes, payload.signature_digest),
        ):
            if hashlib.sha256(contents).hexdigest() != expected:
                raise CommunityFailure("publication_evidence_invalid")
        body = encode(
            submission_request_body(
                SignedArtifact(
                    payload.artifact_bytes, payload.manifest_bytes, payload.signature_bytes
                )
            )
        )
        if len(body) > self.request_bytes:
            raise CommunityFailure("publication_limit")
        return body

    def queue(
        self,
        prepared: PreparedSubmission,
        *,
        consent_token: str,
        consent_version: str,
        operation_id: str,
        scan_id: str | None = None,
        record_consent: bool = False,
    ) -> dict:
        """Revalidate exact approval and commit its bytes before allowing any send."""
        self._clean()
        arguments = dict(
            consent_token=consent_token,
            consent_version=consent_version,
            operation_id=operation_id,
            scan_id=scan_id,
            record_consent=record_consent,
        )
        authority = getattr(self.client, "activity_access", None)

        def operation():
            return self._queue(prepared, **arguments)

        return authority.run(operation) if authority is not None else operation()

    def _queue(self, prepared, **arguments):
        consent_token = arguments["consent_token"]
        consent_version = arguments["consent_version"]
        operation_id = arguments["operation_id"]
        scan_id = arguments["scan_id"]
        record_consent = arguments["record_consent"]
        try:
            if not isinstance(operation_id, str) or not _TOKEN.fullmatch(operation_id):
                raise CommunityFailure("invalid_operation")
            connection = self.client.connection()
            if connection is None or connection.contributor_id is None:
                raise CommunityFailure("not_connected")
            identity = self.client._identity()
            try:
                artifact = decode_artifact_v3(prepared.artifact_bytes)
            except ArtifactV3Error as error:
                raise CommunityFailure("publication_evidence_invalid") from error
            if (artifact.artifact.author.contributor_id, artifact.artifact.author.key_id) != (
                identity.contributor_id,
                identity.key_id,
            ):
                raise CommunityFailure("publication_author_mismatch")
            captured = prepared.source.data()
            reference = prepared.source.reference
            if reference.kind == "scan":
                scan_id = scan_id or reference.identifier
            if (
                scan_id is not None and scan_id not in {scan["id"] for scan in captured["scans"]}
            ) or (reference.kind == "scan" and scan_id != reference.identifier):
                raise CommunityFailure("publication_source_mismatch")
            with write_lock(self.db):
                signed = approve_submission(
                    self.db.get_bind(),
                    prepared,
                    consent_token=consent_token,
                    private_key=identity.private_key,
                )
            if record_consent:
                set_connection(
                    self.db,
                    credential_ref=self.client.home.credential_ref,
                    service_url=identity.service_url,
                    contributor_id=identity.contributor_id,
                    installation_id=identity.installation_id,
                    key_id=identity.key_id,
                    sharing_enabled=True,
                    consent_version=consent_version,
                    consented_at=datetime.now(timezone.utc).isoformat(),
                )
            payload = stage_publication(
                self.db,
                signed=signed,
                preview_digest=hashlib.sha256(prepared.preview_bytes).hexdigest(),
                consent_token=consent_token,
                author_key_id=identity.key_id,
                source_kind=reference.kind,
                source_id=reference.identifier,
                operation_id=operation_id,
                credential_ref=self.client.home.credential_ref,
                service_url=self.client.transport.endpoint.url,
                contributor_id=identity.contributor_id,
                installation_id=identity.installation_id,
                kind="submission",
                revision_id=artifact.artifact.submission_id,
                digest=hashlib.sha256(signed.artifact_bytes).hexdigest(),
                scan_id=scan_id,
                consent_version=consent_version,
                actor_id=(
                    int(self.client.activity_access.reviews.access.principal.user_id)
                    if getattr(self.client, "activity_access", None) is not None
                    else None
                ),
            )
            self._body(self.db.get(CommunityOperationRow, operation_id), payload)
            authority = getattr(self.client, "activity_access", None)
            if authority is not None:
                authority.audit(AuditAction.COMMUNITY_PUBLISH, operation_id)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return self.status(operation_id)

    def status(self, operation_id: str) -> dict:
        """Read a safe local projection without network, keys or payload disclosure."""
        authority = getattr(self.client, "activity_access", None)
        if authority is not None:
            authority.check()
        operation, payload = self._rows(operation_id)
        connection = identity_connection(
            self.db, self.client.home.credential_ref, operation.service_url, populate_existing=True
        )
        output = publication_status(
            operation,
            payload,
            available=connection is not None
            and connection.contributor_id == operation.contributor_id,
        )
        self.db.rollback()
        return output

    def cancel(self, operation_id: str) -> dict:
        """Stop future sends; a submitted operation may already exist remotely."""
        self._clean()
        authority = getattr(self.client, "activity_access", None)
        if authority is not None:
            return authority.run(lambda: self._cancel(operation_id))
        return self._cancel(operation_id)

    def _cancel(self, operation_id):
        try:
            operation, payload = self._rows(operation_id)
            payload.cancel_requested = True
            authority = getattr(self.client, "activity_access", None)
            if authority is not None and operation.state != "acknowledged":
                authority.audit(AuditAction.COMMUNITY_RETRACT, operation_id)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return self.status(operation_id)

    def _accept(self, operation_id, result) -> None:
        """Store the hosted `POST /v1/submissions` result and acknowledge the operation."""
        operation, payload = self._rows(operation_id)
        if (result.submission_id, result.submission_digest) != (
            operation.revision_id,
            operation.digest,
        ):
            raise CommunityFailure("invalid_response")
        payload.result_json = json.dumps(
            result.model_dump(mode="json", by_alias=True), separators=(",", ":")
        )
        payload.error_code = None
        set_operation_state(self.db, operation_id=operation_id, state="acknowledged")
        self.db.commit()

    async def run(self, operation_id: str, *, resend: bool = False) -> dict:
        """Report local status; only an explicit retry sends the saved bytes.

        The service keeps no readable operation status, so a check never sends: a
        retry with the same `submit-<submissionId>` key and bytes returns the stored
        result. Retired `publish` operations are never sent.
        """
        self._clean()
        authority = getattr(self.client, "activity_access", None)
        if authority is not None:
            authority.check()
        try:
            operation, payload = self._rows(operation_id)
            if (
                not resend
                or operation.kind != "submission"
                or operation.state == "acknowledged"
                or payload.cancel_requested
                or payload.error_code in _REJECTED
            ):
                return self.status(operation_id)
            connection = identity_connection(
                self.db,
                self.client.home.credential_ref,
                operation.service_url,
                populate_existing=True,
            )
            if connection is None or connection.contributor_id is None:
                raise CommunityFailure("not_connected")
            identity = self.client._identity()
            if (operation.service_url, operation.contributor_id, operation.installation_id) != (
                self.client.transport.endpoint.url,
                identity.contributor_id,
                identity.installation_id,
            ):
                raise CommunityFailure("identity_conflict")
            if (
                not connection.sharing_enabled
                or operation.consent_version != connection.consent_version
            ):
                raise CommunityFailure("sharing_consent_required")
            body = self._body(operation, payload)
            submission_id = operation.revision_id

            def submit():
                payload.error_code = None
                set_operation_state(self.db, operation_id=operation_id, state="submitted")
                if authority is not None:
                    authority.audit(AuditAction.COMMUNITY_RETRY, operation_id)

            authority.run(submit) if authority is not None else submit()
            self.db.commit()
            result = await MasterService(self.client).submit(body, submission_id)
            self._accept(operation_id, result)
        except CommunityFailure as error:
            self.db.rollback()
            operation, payload = self._rows(operation_id)
            if operation.state != "acknowledged":
                payload.error_code = None if error.code == "not_connected" else error.code
                if error.code in _REJECTED:
                    set_operation_state(self.db, operation_id=operation_id, state="failed")
                self.db.commit()
        except (asyncio.CancelledError, Exception):
            self.db.rollback()
            raise
        return self.status(operation_id)
