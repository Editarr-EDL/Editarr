"""Session-bound exact public previews with explicit, independently approved donation."""

import hashlib
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import TypeAdapter

from scanner.db import (
    CommunityOperationRow,
    CommunityPublicationRow,
)
from scanner.formats.community.codec import ArtifactError, ResourceBounds
from scanner.modeling.media_identity import MediaIdentity

from .client import CommunityClient
from .donation_projection import DonationOptions
from .donation_sources import SourceRef
from .donations import PreparedDonation, prepare_donation
from .publications import PublicationOutbox
from .sharing_context import TERMS_TEXT, TERMS_VERSION, SharingSource
from .transport import CommunityFailure


@dataclass(frozen=True)
class PreviewRecord:
    owner: str
    scope: str
    expires_at: float
    prepared: PreparedDonation
    operation_id: str
    contribution_id: str


class PreviewStore:
    """Keep bounded private buffers only until explicit approval or preview expiry."""

    def __init__(self, *, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.records: dict[str, PreviewRecord] = {}

    def put(self, record: PreviewRecord) -> str:
        with self.lock:
            self.records = {
                key: value for key, value in self.records.items() if value.expires_at > self.clock()
            }
            total = sum(
                len(item.prepared.source.private_bytes)
                + len(item.prepared.preview_bytes)
                + len(item.prepared.artifact_bytes)
                + len(item.prepared.manifest_bytes)
                for item in (*self.records.values(), record)
            )
            if len(self.records) >= 16 or total > 64 * 1024 * 1024:
                raise CommunityFailure("preview_capacity", retryable=True)
            identifier = secrets.token_urlsafe(32)
            self.records[identifier] = record
            return identifier

    def get(self, identifier: str, owner: str, scope: str) -> PreviewRecord:
        with self.lock:
            record = self.records.get(identifier)
            if record is None or record.expires_at <= self.clock():
                self.records.pop(identifier, None)
                raise CommunityFailure("preview_expired")
            if (record.owner, record.scope) != (owner, scope):
                raise CommunityFailure("preview_unavailable")
            return record

    def discard(self, identifier: str, owner: str, scope: str):
        with self.lock:
            self.get(identifier, owner, scope)
            self.records.pop(identifier, None)


previews = PreviewStore()


class SharingService:
    def __init__(
        self,
        client: CommunityClient,
        *,
        bounds: ResourceBounds,
        source_limit: int,
        request_bytes: int,
        store=previews,
        authorize_source=None,
    ):
        if type(source_limit) is not int or source_limit <= 0:
            raise CommunityFailure("preview_not_configured")
        self.client, self.db = client, client.db
        self.bounds, self.source_limit, self.store = bounds, source_limit, store
        self.source = SharingSource(self.db, bounds, source_limit)
        self.outbox = PublicationOutbox(client, request_bytes=request_bytes)
        self.authorize_source = authorize_source
        self.scope = hashlib.sha256(
            (
                str(client.data_dir.resolve())
                + "\0"
                + client.transport.endpoint.url
                + "\0"
                + client.home.credential_ref
            ).encode()
        ).hexdigest()

    def _identity(self):
        connection = self.client.connection()
        if connection is None or connection.contributor_id is None:
            self.db.rollback()
            raise CommunityFailure("not_connected")
        identity = self.client._identity()
        self.db.rollback()
        return identity

    def _capture(self, source):
        return self.source.capture(source)

    def _authorize(self, source):
        if self.authorize_source is not None:
            self.authorize_source(source)

    def _source_terms(self, captured):
        return self.source.terms(captured)

    def context(self, source: SourceRef) -> dict:
        """Describe eligible analyzed sources and recent durable jobs without uploading."""
        self._authorize(source)
        self._identity()
        context = self.source.describe(source)
        latest_query = (
            self.db.query(CommunityPublicationRow)
            .filter_by(source_kind=source.kind, source_id=source.identifier)
            .join(
                CommunityOperationRow,
                CommunityOperationRow.operation_id == CommunityPublicationRow.operation_id,
            )
        )
        authority = getattr(self.client, "activity_access", None)
        if authority is not None:
            latest_query = latest_query.filter(
                CommunityOperationRow.owner_id == authority.reviews.access.principal.user_id
            )
        latest = (
            latest_query.order_by(
                CommunityOperationRow.updated_at.desc(), CommunityPublicationRow.operation_id.desc()
            )
            .limit(50)
            .all()
        )
        jobs = [item.operation_id for item in latest]
        self.db.rollback()
        return {
            **context,
            "jobs": [self.outbox.status(identifier) for identifier in jobs],
        }

    def prepare(
        self,
        source: SourceRef,
        *,
        owner: str,
        name: str,
        media: MediaIdentity,
        audio_source_scan_id: str | None = None,
    ) -> dict:
        """Freeze private evidence, returning only its exact allowlisted public preview."""
        self._authorize(source)
        identity = self._identity()
        captured = self._capture(source)
        self._source_terms(captured)
        data = captured.data()
        duration = (
            data["synchronization"][0]["end_seconds"]
            if data["synchronization"]
            else data["root"].get("duration_seconds")
        )
        if duration is None or duration <= 0:
            raise CommunityFailure("source_duration_unknown")
        try:
            prepared = prepare_donation(
                self.db.get_bind(),
                source,
                DonationOptions(
                    submission_id=str(uuid.uuid4()),
                    name=name,
                    contributor_id=identity.contributor_id,
                    key_id=identity.key_id,
                    created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    media=media,
                    duration_seconds=duration,
                    sharing_terms=TERMS_VERSION,
                    audio_source_scan_id=audio_source_scan_id,
                ),
                bounds=self.bounds,
                source_limit=self.source_limit,
            )
        except ArtifactError as error:
            raise CommunityFailure("preview_invalid") from error
        record = PreviewRecord(
            owner,
            self.scope,
            self.store.clock() + 900,
            prepared,
            "publish-" + str(uuid.uuid4()),
            "contribution-" + str(uuid.uuid4()),
        )
        preview_id = self.store.put(record)
        return {
            "previewId": preview_id,
            "expiresAt": int(record.expires_at),
            "preview": json.loads(prepared.preview_bytes),
            "terms": {"version": TERMS_VERSION, "text": TERMS_TEXT},
        }

    def confirmed_media(self, source: SourceRef) -> MediaIdentity:
        """Read the source's persisted confirmed association without a file-identity proof."""
        self._authorize(source)
        data = self._capture(source).data()
        stored = data["media"][0].get("confirmed_identity_json") if data["media"] else None
        if not stored:
            raise CommunityFailure("media_identity_required")
        return TypeAdapter(MediaIdentity).validate_json(stored)

    async def approve(self, preview_id: str, *, owner: str, confirmed: bool) -> dict:
        """Revalidate current access and queue the original buffers, never send them."""
        if confirmed is not True:
            raise CommunityFailure("confirmation_required")
        record = self.store.get(preview_id, owner, self.scope)
        self._authorize(record.prepared.source.reference)
        existing = self.db.get(CommunityOperationRow, record.operation_id)
        self.db.rollback()
        if existing is not None:
            return self.outbox.status(record.operation_id)
        if self._capture(record.prepared.source.reference).guard != record.prepared.source.guard:
            raise CommunityFailure("preview_stale")
        self._source_terms(record.prepared.source)
        self._identity()
        await self.client.refresh()
        self.db.rollback()
        try:
            return self.outbox.queue(
                record.prepared,
                consent_token=record.prepared.consent_token,
                consent_version=TERMS_VERSION,
                operation_id=record.operation_id,
                contribution_id=record.contribution_id,
                record_consent=True,
            )
        except ArtifactError as error:
            raise CommunityFailure("preview_stale") from error
