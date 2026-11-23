"""Authenticated catalog-bound downloads with separately reconcilable local commits."""

import asyncio
import json
import secrets
from dataclasses import asdict

from starlette.concurrency import run_in_threadpool

from scanner.db import (
    CommunityImportRow,
    CommunityOperationRow,
    MediaItemRow,
    ReviewScanMembershipRow,
    SavedReviewRow,
    ScanRow,
)
from scanner.formats.community.codec import ResourceBounds
from scanner.modeling.community_matching import classify
from scanner.modeling.model_community_downloads import DownloadRequest, DownloadStatusRequest
from scanner.modeling.model_community_lookup import LookupPage
from scanner.persistence.repository.community_import_projection import LocalImportTarget
from scanner.persistence.repository.community_import_targets import (
    approved_target_channels,
    pending_target_channels,
    stored_target,
)
from scanner.persistence.repository.community_imports import import_verified_download
from scanner.repository import ReviewMembershipError, get_jellyfin_credentials
from scanner.runtime.media_fingerprints import cached_content_digest, sampled_media_fingerprint

from .downloads import fetch_verified_download
from .lookup import RESPONSE_BYTES, _validated
from .lookup_targets import MissingTarget, capture_target
from .receipts import acknowledge_committed_import
from .transport import CommunityFailure


async def drained_thread(function, *args, **kwargs):
    """Do not close the caller's Session while a cancelled worker is still using it."""
    work = asyncio.create_task(run_in_threadpool(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        await asyncio.gather(work, return_exceptions=True)
        raise


def media_row(db, target):
    credentials = get_jellyfin_credentials(db)
    if not credentials:
        raise CommunityFailure("target_unavailable")
    row = (
        db.query(MediaItemRow)
        .filter_by(server_id=credentials[0], jellyfin_id=target.item_id)
        .one_or_none()
    )
    if row is None:
        raise MissingTarget()
    return row


class CommunityDownloads:
    def __init__(self, client, *, bounds: ResourceBounds, response_bytes: int):
        self.client = client
        self.bounds = bounds
        self.response_bytes = response_bytes

    async def match_choice(self, identity, captured, choice, *, should_cancel=None):
        """Revalidate one revision against the authenticated current match catalog."""
        cursor = None
        seen = set()
        selected = None
        for _page in range(6):
            captured.check(should_cancel)
            value = await self.client.transport.request(
                "POST",
                "/v1/matches",
                identity=identity,
                body=json.dumps(
                    {
                        "version": 1,
                        "evidence": asdict(captured.evidence),
                        "pageSize": 16,
                        "cursor": cursor,
                        "refresh": False,
                    },
                    separators=(",", ":"),
                ).encode(),
                operation_id=secrets.token_urlsafe(16),
                response_bytes=RESPONSE_BYTES,
            )
            page = _validated(LookupPage, value)
            if len(page.candidates) > 16 or len(
                {candidate.revisionId for candidate in page.candidates}
            ) != len(page.candidates):
                raise CommunityFailure("invalid_response")
            selected = next(
                (
                    candidate
                    for candidate in page.candidates
                    if candidate.revisionId == choice.revisionId
                ),
                None,
            )
            if selected is not None:
                break
            if not page.nextCursor or page.nextCursor in seen:
                break
            cursor = page.nextCursor
            seen.add(cursor)
        captured.check(should_cancel)
        if selected is None:
            raise CommunityFailure("candidate_unavailable")
        if (selected.contributionId, selected.artifactDigest) != (
            choice.contributionId,
            choice.artifactDigest,
        ):
            raise CommunityFailure("candidate_changed")
        expected = json.loads(json.dumps(asdict(classify(captured.evidence, selected.evidence))))
        if (
            selected.compatibility.model_dump() != expected
            or selected.summary.submission_id != selected.revisionId
        ):
            raise CommunityFailure("invalid_response")
        return selected

    async def run(self, body: DownloadRequest, *, should_cancel=None):
        identity = self.client._identity()
        if not identity.contributor_id:
            raise CommunityFailure("not_connected")
        if identity.pending_replacement is not None:
            raise CommunityFailure("rotation_pending")
        request = body.lookup
        if not request.target_key or request.cursor or request.refresh:
            raise CommunityFailure("invalid_target")
        capture_arguments = {"should_cancel": should_cancel}
        if getattr(self.client, "authorize_target", None) is not None:
            capture_arguments["authorize"] = self.client.authorize_target
        captured = await drained_thread(
            capture_target, self.client.db, request, **capture_arguments
        )
        captured.check(should_cancel)
        if captured.key != request.target_key:
            raise CommunityFailure("target_changed")
        media = media_row(self.client.db, request.target)
        selected = await self.match_choice(
            identity, captured, body.choice, should_cancel=should_cancel
        )
        downloaded = await fetch_verified_download(
            self.client.transport,
            identity,
            contribution_id=selected.contributionId,
            revision_id=selected.revisionId,
            bounds=self.bounds,
            response_bytes=self.response_bytes,
        )
        captured.check(should_cancel)
        if downloaded.verified.manifest.manifest.artifact_digest != selected.artifactDigest:
            raise CommunityFailure("candidate_changed")
        latest = self.client._identity()
        if (latest.key_id, latest.contributor_id, latest.pending_replacement) != (
            identity.key_id,
            identity.contributor_id,
            None,
        ):
            raise CommunityFailure("target_changed")
        fingerprint = known_content_sha256 = None
        if body.stage_for_verification:
            try:
                fingerprint = await drained_thread(sampled_media_fingerprint, captured.path)
                known_content_sha256 = await drained_thread(cached_content_digest, captured.path)
            except (OSError, RuntimeError, ValueError) as exc:
                raise CommunityFailure("target_changed") from exc
            captured.check(should_cancel)
        target = LocalImportTarget(
            media.id,
            request.target.media_source_id,
            str(captured.path),
            captured.evidence,
            tuple(body.choice.channels),
            captured.key,
            stage_for_verification=body.stage_for_verification,
            fingerprint=fingerprint,
            known_content_sha256=known_content_sha256,
        )

        def commit():
            captured.check(should_cancel)
            return import_verified_download(
                self.client.db,
                downloaded,
                target,
                credential_ref=self.client.home.credential_ref,
                bounds=self.bounds,
                confirm_uncertain=body.confirmed,
            )

        authority = getattr(self.client, "activity_access", None)
        result = (
            await drained_thread(authority.run, commit)
            if authority is not None
            else await drained_thread(commit)
        )
        actor_id = (
            int(authority.reviews.access.principal.user_id) if authority is not None else None
        )
        reviewed = used_in_review(
            self.client.db,
            result.scan.id,
            media.id,
            request.target.media_source_id,
            user_id=actor_id,
        )
        receipt_state = await acknowledge_committed_import(
            self.client, result.import_id, result.receipt_state
        )
        response = {
            "version": 1,
            "revisionId": selected.revisionId,
            "status": "reused" if result.reused else "imported",
            "scan": result.scan.model_dump(mode="json"),
            "receiptState": receipt_state,
            "reviewRequired": result.review_required,
            "reviewed": reviewed,
        }
        if result.verification_pending:
            response["verificationPending"] = True
        return response


def used_in_review(db, scan_id, media_item_id, media_source_id, *, user_id=None):
    query = (
        db.query(ReviewScanMembershipRow)
        .join(SavedReviewRow, SavedReviewRow.id == ReviewScanMembershipRow.review_id)
        .filter(
            ReviewScanMembershipRow.scan_id == scan_id,
            SavedReviewRow.media_item_id == media_item_id,
            SavedReviewRow.media_source_id == (media_source_id or ""),
        )
    )
    if user_id is not None:
        query = query.filter(SavedReviewRow.owner_id == user_id)
    return query.first() is not None


def download_status(db, service_url, body: DownloadStatusRequest, *, user_id=None):
    """Read committed evidence locally, even when community access is unavailable."""
    media = media_row(db, body.target)
    outcomes = []
    for choice in body.choices:
        imported = (
            db.query(CommunityImportRow)
            .filter(
                CommunityImportRow.service_url == service_url,
                CommunityImportRow.contribution_id == choice.contributionId,
                CommunityImportRow.revision_id == choice.revisionId,
                CommunityImportRow.digest.in_(
                    [choice.artifactDigest, "sha256:" + choice.artifactDigest]
                ),
            )
            .one_or_none()
        )
        outcome = {"revisionId": choice.revisionId, "status": "not_imported"}
        scan = db.get(ScanRow, imported.scan_id) if imported else None
        if scan is not None:
            association = stored_target(db, scan, media.id, body.target.media_source_id)
            if (
                association
                and json.loads(association.target_evidence_json).get("lookup_target_key")
                == body.target_key
            ):
                try:
                    approved = approved_target_channels(association, scan)
                except ReviewMembershipError:
                    pass
                else:
                    pending = pending_target_channels(association)
                    pending_here = set(choice.channels) & pending
                    if not approved and not pending_here:
                        outcomes.append(outcome)
                        continue
                    operations = db.query(CommunityOperationRow).filter_by(
                        kind="receipt",
                        service_url=service_url,
                        scan_id=scan.id,
                        revision_id=imported.revision_id,
                        digest=imported.digest,
                    )
                    if user_id is not None:
                        operations = operations.filter_by(owner_id=user_id)
                    operation = operations.first()
                    outcome.update(
                        status=(
                            "imported"
                            if set(choice.channels) <= set(approved) | pending
                            else "partial"
                        ),
                        scanId=imported.scan_id,
                        approvedChannels=approved,
                        reviewed=used_in_review(
                            db, scan.id, media.id, body.target.media_source_id, user_id=user_id
                        ),
                        receiptState=operation.state if operation else "unavailable",
                        reviewRequired=association.review_required,
                    )
                    if pending_here:
                        outcome["verificationPending"] = True
                        outcome["pendingChannels"] = sorted(pending_here)
        outcomes.append(outcome)
    return {"version": 1, "outcomes": outcomes}
