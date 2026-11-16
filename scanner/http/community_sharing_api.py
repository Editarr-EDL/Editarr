"""Privileged manual sharing inputs, exact previews and durable approval."""

import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from scanner.db import CommunityOperationRow, CommunityPublicationRow, get_db
from scanner.formats.community.codec import ResourceBounds
from scanner.integrations.community.activity_access import PUBLISH_REVIEW, ActivityAccess
from scanner.integrations.community.donation_sources import SourceRef
from scanner.integrations.community.publication_status import publication_status
from scanner.integrations.community.sharing import SharingService
from scanner.integrations.community.sharing_context import SharingSource
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_work_refs import identity_from_reference
from scanner.modeling.media_identity import EpisodeNumber, normalize_manual
from scanner.modeling.model_media_identity import ConfirmedEpisodeNumber
from scanner.persistence.repository.repository_community import identity_connection, identity_ref
from scanner.persistence.repository.review_access import VIEW, ReviewAccess
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access
from scanner.security.private_identity_files import IdentityUnavailable
from scanner.settings.config import settings

router = APIRouter(prefix="/api/community/sharing", tags=["community"])


class PublicReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["tmdb", "tvdb"]
    entity_type: Literal["movie", "series", "episode"]
    reference: str = Field(min_length=1, max_length=2048)
    series_reference: str | None = Field(default=None, max_length=2048)
    episode_numbers: list[ConfirmedEpisodeNumber] = Field(default_factory=list, max_length=32)

    def identity(self):
        identity = identity_from_reference(self.provider, self.entity_type, self.reference)
        series = (
            identity_from_reference(self.provider, "series", self.series_reference).identifiers
            if self.series_reference
            else identity.series_identifiers
        )
        if identity.series_identifiers and series != identity.series_identifiers:
            raise ValueError("Conflicting episode series")
        numbers = tuple(EpisodeNumber(**item.model_dump()) for item in self.episode_numbers)
        if identity.episode_numbers and numbers and identity.episode_numbers != numbers:
            raise ValueError("Conflicting episode numbering")
        if self.entity_type != "episode" and (series or numbers):
            raise ValueError("Episode context requires an episode")
        return normalize_manual(
            identity.entity_type,
            identity.identifiers,
            series_identifiers=series,
            episode_numbers=numbers or identity.episode_numbers,
            confirmed=True,
        )


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    identity_confirmed: StrictBool
    media: PublicReference | None = None
    audio_source_scan_id: str | None = Field(default=None, max_length=128)


class PreviewApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool


def sharing_bounds() -> ResourceBounds:
    if not settings.community_preview_bounds or not settings.community_preview_source_limit:
        raise CommunityFailure("preview_not_configured")
    try:
        return ResourceBounds(**settings.community_preview_bounds)
    except (TypeError, ValueError) as error:
        raise CommunityFailure("preview_not_configured") from error


def sharing_service(db: Session, access: PrincipalAccess) -> SharingService:
    from .community_identity_api import local_client

    if not settings.community_publication_request_bytes:
        raise CommunityFailure("preview_not_configured")
    bounds = sharing_bounds()
    client = local_client(db, access)

    def authorize_source(source):
        action = PUBLISH_REVIEW if source.kind == "review" else C.COMMUNITY_PUBLISH_SCAN_EVIDENCE
        authority = ActivityAccess(ReviewAccess(db, access), source, action)
        authority.check()
        authority.bind(client)

    return SharingService(
        client,
        bounds=bounds,
        source_limit=settings.community_preview_source_limit,
        request_bytes=settings.community_publication_request_bytes,
        authorize_source=authorize_source,
    )


def _owner(request: Request) -> str:
    cookie = request.cookies.get(settings.auth_cookie_name)
    if not cookie:
        raise CommunityFailure("authentication_required")
    return hashlib.sha256(cookie.encode()).hexdigest()


def _review_source(db, access, kind, source_id):
    if kind == "review":
        from scanner.persistence.repository.review_access import ReviewAccess
        from scanner.persistence.repository.review_repairs import require_repaired

        facts = ReviewAccess(db, access).visible(source_id)
        require_repaired(db, source_id)
        return facts.owner.user_id if facts.owner else None
    return None


def source_publications(db, access, source):
    """List only recent jobs that pass current original-contributor management authority."""
    from scanner.integrations.community.activity_access import CHECK

    identifiers = [
        identifier
        for (identifier,) in (
            db.query(CommunityOperationRow.operation_id)
            .join(CommunityPublicationRow)
            .filter(
                CommunityPublicationRow.source_kind == source.kind,
                CommunityPublicationRow.source_id == source.identifier,
            )
            .order_by(
                CommunityOperationRow.updated_at.desc(), CommunityOperationRow.operation_id.desc()
            )
            .limit(50)
            .all()
        )
    ]
    db.rollback()
    jobs = []
    for identifier in identifiers:
        operation = db.get(CommunityOperationRow, identifier)
        if operation is None or operation.owner_id is None:
            db.rollback()
            continue
        owner_id = operation.owner_id
        authority = ActivityAccess(ReviewAccess(db, access), source, CHECK, owner_id)

        def status():
            operation = db.get(CommunityOperationRow, identifier, populate_existing=True)
            payload = db.get(CommunityPublicationRow, identifier, populate_existing=True)
            if (
                operation is None
                or payload is None
                or operation.owner_id != owner_id
                or (payload.source_kind, payload.source_id) != (source.kind, source.identifier)
            ):
                from scanner.persistence.repository.review_access import not_found

                raise not_found()
            reference = identity_ref(db, owner_id)
            connection = (
                identity_connection(db, reference, operation.service_url) if reference else None
            )
            available = (
                connection is not None and connection.contributor_id == operation.contributor_id
            )
            return publication_status(operation, payload, available=available)

        try:
            jobs.append(authority.run(status))
        except AccessDenied as error:
            db.rollback()
            if error.code not in {AccessErrorCode.NOT_FOUND, AccessErrorCode.CAPABILITY_DENIED}:
                raise
            visibility = VIEW if source.kind == "review" else C.SCANS_FINDINGS_READ
            ActivityAccess(ReviewAccess(db, access), source, visibility).check()
    return jobs


@router.get("/sources/{kind}/{source_id}")
async def source_context(
    kind: Literal["scan", "review"],
    source_id: str,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    capabilities = {
        C.COMMUNITY_PUBLICATIONS_CHECK_OWN,
        C.COMMUNITY_PUBLICATIONS_CHECK_OTHER,
    } | (
        {C.COMMUNITY_PUBLISH_SCAN_EVIDENCE}
        if kind == "scan"
        else {C.COMMUNITY_PUBLISH_OWN_REVIEW, C.COMMUNITY_PUBLISH_OTHER_REVIEW}
    )
    if access.capabilities.isdisjoint(capabilities):
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    owner_id = _review_source(db, access, kind, source_id)

    async def action():
        from .community_identity_api import local_client

        bounds = sharing_bounds()
        source = SourceRef(kind, source_id)
        visibility = VIEW if kind == "review" else C.SCANS_FINDINGS_READ
        authority = ActivityAccess(ReviewAccess(db, access), source, visibility)
        await run_in_threadpool(authority.check)
        description = SharingSource(db, bounds, settings.community_preview_source_limit)
        context = await run_in_threadpool(description.describe, source)
        jobs = await run_in_threadpool(source_publications, db, access, source)
        access_error = None
        try:
            client = local_client(db, authority.reviews.access)
            await run_in_threadpool(authority.bind, client)
            await client.refresh()
        except CommunityFailure as error:
            access_error = error.code
        except IdentityUnavailable as error:
            access_error = "identity_" + error.status
        db.rollback()
        return {
            **context,
            "jobs": jobs,
            "sourceOwnerId": owner_id,
            "accessVerified": access_error is None,
            "accessError": access_error,
        }

    return await _result(action)


@router.post("/sources/{kind}/{source_id}/preview")
async def prepare_preview(
    kind: Literal["scan", "review"],
    source_id: str,
    body: PreviewInput,
    request: Request,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    _review_source(db, access, kind, source_id)

    def action():
        if not body.identity_confirmed:
            raise CommunityFailure("identity_confirmation_required")
        sharing = sharing_service(db, access)
        source = SourceRef(kind, source_id)
        try:
            if body.media:
                media = body.media.identity()
            else:
                saved = sharing.confirmed_media(source)
                media = normalize_manual(
                    saved.entity_type,
                    saved.identifiers,
                    series_identifiers=saved.series_identifiers,
                    episode_numbers=saved.episode_numbers,
                    episode_span=saved.episode_span,
                    release=saved.release,
                    confirmed=True,
                )
            if media.input_requirement:
                raise CommunityFailure("media_identity_required")
        except ValueError as error:
            raise CommunityFailure("invalid_media_reference") from error
        return sharing.prepare(
            source,
            owner=_owner(request),
            name=body.name,
            media=media,
            audio_source_scan_id=body.audio_source_scan_id,
        )

    return await _result(lambda: run_in_threadpool(action))


@router.post("/previews/{preview_id}/approve")
async def approve_preview(
    preview_id: str,
    body: PreviewApproval,
    request: Request,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    return await _result(
        lambda: sharing_service(db, access).approve(
            preview_id, owner=_owner(request), confirmed=body.confirmed
        )
    )


@router.delete("/previews/{preview_id}")
async def discard_preview(
    preview_id: str,
    request: Request,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    from .community_api import _result

    def action():
        sharing = sharing_service(db, access)
        sharing.store.discard(preview_id, _owner(request), sharing.scope)
        return {"discarded": True}

    return await _result(action)
