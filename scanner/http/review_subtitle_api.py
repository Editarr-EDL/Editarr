"""Authorized discovery, preview, and publication of named subtitle exports."""

import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from scanner import repository
from scanner.integrations.jellyfin import JellyfinClient, JellyfinError
from scanner.modeling.model_subtitles import (
    SubtitlePreview,
    SubtitlePublication,
    SubtitlePublishRequest,
    SubtitleSourceRequest,
    SubtitleSources,
    SubtitleSourcesRequest,
)
from scanner.persistence.repository.export_recovery import publication_lock
from scanner.persistence.repository.review_access import VIEW, ReviewAccess
from scanner.persistence.repository.review_policy import ReviewPolicyConflict
from scanner.persistence.repository.review_subtitles import (
    SubtitleError,
    render_subtitle,
    require_same_subtitle_input,
    review_subtitle_evidence,
    subtitle_catalog,
    subtitle_publication_intent,
)
from scanner.runtime.subtitle_publication_names import SubtitleNameError
from scanner.runtime.subtitle_sidecars import (
    SubtitleConflict,
    media_slot,
    publish_named_sidecar,
)
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.targets import AccountRef, ActionPair, MediaRef, ResourceRef

from .review_access import review_caller
from .review_export_api import _error
from .review_target_api import _review_http_error
from .review_targets import capture_target

router = APIRouter()
_DOWNLOAD = ActionPair(C.EXPORTS_PERSONAL_DOWNLOAD_OWN, C.REVIEWS_EXPORT_OTHER)
_PERSONAL_WRITE = ActionPair(C.EXPORTS_PERSONAL_PUBLISH_OWN, C.REVIEWS_EXPORT_OTHER)
_DEFAULT_WRITE = ActionPair(C.REVIEWS_VIEW_OWN, C.REVIEWS_EXPORT_OTHER)


def _require_review_export(access, target: ResourceRef) -> None:
    """Apply saved-review export eligibility to discovery, preview, and shared publication."""
    principal = access.principal
    self_target = ResourceRef(AccountRef(principal.user_id, principal.account.role), target.media)

    def allowed(action, resource):
        return isinstance(decide(access, action, resource), Allowed)

    if (
        (allowed(_DOWNLOAD, target) and allowed(C.EXPORTS_PERSONAL_DOWNLOAD_OWN, self_target))
        or (
            allowed(_PERSONAL_WRITE, target)
            and allowed(C.EXPORTS_PERSONAL_PUBLISH_OWN, self_target)
        )
        or (
            target.owner is not None
            and target.owner.user_id != principal.user_id
            and allowed(_PERSONAL_WRITE, target)
        )
        or (
            allowed(_DEFAULT_WRITE, target)
            and allowed(C.EXPORTS_DEFAULT_PUBLISH, MediaRef(target.media))
        )
    ):
        return
    require(access, _DOWNLOAD, target)
    require(access, C.EXPORTS_PERSONAL_DOWNLOAD_OWN, self_target)


def _capture(caller: ReviewAccess, review_id: str):
    caller.visible(review_id)
    return capture_target(caller, repository.get_saved_review(caller.db, review_id).target)


def _run(caller: ReviewAccess, review_id: str, target, operation):
    return caller.run(
        review_id,
        VIEW,
        operation,
        prepared=target,
        authorize=lambda ref: _require_review_export(caller.access, ref),
        capture_review_media=True,
    )


def _catalog(caller, review_id, target, request):
    def operation(db, source_id):
        evidence = review_subtitle_evidence(db, source_id, target, request)
        return subtitle_catalog(
            db,
            evidence,
            target,
            request.audio_source_id,
            review_id=source_id,
            account=caller.access.principal.account,
        )

    return _run(caller, review_id, target, operation)


def _mapped_error(exc):
    if isinstance(exc, SubtitleError):
        return _review_http_error(exc.code, str(exc), exc.status)
    if isinstance(exc, SubtitleNameError):
        return _review_http_error(exc.code, str(exc), 409)
    if isinstance(exc, SubtitleConflict):
        return _review_http_error("subtitle_publication_conflict", str(exc), 409)
    if isinstance(exc, OSError):
        return _review_http_error(
            "subtitle_write_failed", "Could not access the media's subtitle files", 409
        )
    return _error(exc)


@router.post("/api/reviews/{review_id}/subtitles/sources", response_model=SubtitleSources)
def subtitle_sources(
    review_id: str,
    request: SubtitleSourcesRequest,
    response: Response,
    caller: ReviewAccess = Depends(review_caller),
):
    """Return only admissible source metadata; local filenames are never source identifiers."""
    response.headers["cache-control"] = "private, no-store"
    try:
        return _catalog(caller, review_id, _capture(caller, review_id), request).response
    except ReviewPolicyConflict:
        raise
    except (ValueError, OSError) as exc:
        raise _mapped_error(exc) from exc


@router.post("/api/reviews/{review_id}/subtitles/preview", response_model=SubtitlePreview)
def subtitle_preview(
    review_id: str,
    request: SubtitleSourceRequest,
    response: Response,
    caller: ReviewAccess = Depends(review_caller),
):
    """Render a capped plain-text sample from the exact saved review and source."""
    response.headers["cache-control"] = "private, no-store"
    try:
        target = _capture(caller, review_id)
        catalog = _catalog(caller, review_id, target, request)
        output = render_subtitle(
            review_id,
            request,
            catalog,
            request.source_id,
            lambda: _language_tokens(caller.db, target.source.server_id),
        )
        changed = len(output.redaction.replacements)
        return SubtitlePreview(
            preview_token=output.token,
            source=output.source,
            cues=output.redaction.preview(),
            changed_cues=changed,
            unchanged_cues=len(output.redaction.document.cues) - changed,
            track_title=catalog.response.track_title,
            filename=output.destination.name if output.destination else None,
            publication_block_reason=(
                str(output.publication_error) if output.publication_error else None
            ),
        )
    except ReviewPolicyConflict:
        raise
    except (ValueError, OSError) as exc:
        raise _mapped_error(exc) from exc


@router.post("/api/reviews/{review_id}/subtitles/publish", response_model=SubtitlePublication)
def subtitle_publish(
    review_id: str,
    request: SubtitlePublishRequest,
    response: Response,
    caller: ReviewAccess = Depends(review_caller),
):
    """Require the exact preview, then reauthorize and atomically publish one sidecar."""
    response.headers["cache-control"] = "private, no-store"
    try:
        target = _capture(caller, review_id)
        catalog = _catalog(caller, review_id, target, request)
        output = render_subtitle(
            review_id,
            request,
            catalog,
            request.source_id,
            lambda: _language_tokens(caller.db, target.source.server_id),
        )
        if output.publication_error is not None:
            raise output.publication_error
        assert output.token is not None and output.destination is not None
        if not hmac.compare_digest(output.token, request.preview_token):
            raise SubtitleError(
                "subtitle_source_changed", "The subtitle source changed. Preview it again."
            )

        def operation(db, source_id):
            current = review_subtitle_evidence(db, source_id, target, request)
            require_same_subtitle_input(catalog.evidence, current, output)
            publication = subtitle_publication_intent(
                db,
                source_id,
                caller.access.principal.account,
                current,
            )
            if publication != catalog.publication:
                raise SubtitleError(
                    "subtitle_source_changed",
                    "The subtitle publication changed. Preview it before publishing.",
                )
            return publish_named_sidecar(
                current.media,
                media_slot(current.media, current.version),
                output.source,
                output.content,
                publication,
                output.destination,
            )

        with publication_lock(caller.db):
            filename, replaced = _run(caller, review_id, target, operation)
        refresh_pending = not _refresh(
            caller.db, target.source.item_id, target.source.server_id, target.source.media_source_id
        )
        return SubtitlePublication(
            source=output.source.model_copy(update={"published_filename": filename}),
            filename=filename,
            replaced=replaced,
            refresh_pending=refresh_pending,
        )
    except ReviewPolicyConflict:
        raise
    except (ValueError, OSError) as exc:
        raise _mapped_error(exc) from exc


def _language_tokens(db, server_id: str) -> frozenset[str]:
    credentials = repository.get_jellyfin_credentials(db)
    if credentials is not None and credentials[0] == server_id:
        try:
            with JellyfinClient(*credentials) as client:
                return client.subtitle_language_tokens()
        except JellyfinError:
            pass
    raise SubtitleNameError(
        "subtitle_language_unavailable",
        "Editarr could not check which subtitle language tags Jellyfin recognizes. "
        "Check the Jellyfin connection and try again.",
    )


def _refresh(db, item_id: str, server_id: str, media_source_id: str | None) -> bool:
    """Ask Jellyfin to discover the version's new sidecar after the committed write."""
    credentials = repository.get_jellyfin_credentials(db)
    if credentials is None or credentials[0] != server_id:
        return False
    versions = [item_id]
    if media_source_id and media_source_id != item_id:
        try:
            versions.insert(0, UUID(media_source_id).hex)
        except ValueError:
            pass
    try:
        with JellyfinClient(*credentials) as client:
            for version in versions:
                try:
                    client.refresh_sidecars(version)
                    return True
                except JellyfinError:
                    continue
    except JellyfinError:
        pass
    return False
