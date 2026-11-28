"""Render filter clips from the current draft and its authorized media target."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from scanner import api as root
from scanner import repository
from scanner.db import (
    MediaItemRow,
    ReviewCommunitySourceRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.modeling.model_core import MAX_PERSISTED_INTEGER, ReviewWriteModel
from scanner.models import EditAction, ReviewTargetRequest
from scanner.persistence.repository.community_import_targets import (
    matches_target,
    review_duration,
    validate_target_role,
    verified_target,
)
from scanner.persistence.repository.media_identity_store import (
    find_title_id,
    get_version,
    review_version_id,
)
from scanner.persistence.repository.review_access import PREVIEW, ReviewAccess, not_found
from scanner.persistence.repository.review_version_evidence import (
    ReviewMediaProof,
    capture_media_proof,
    use_review_media_proof,
    version_file_matches,
)
from scanner.runtime.scan_version import ScanVersionChanged, file_generation, generation_token
from scanner.runtime.version_reuse_audio import select_target_audio
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.targets import ActionPair, ResourceRef

from .review_access import review_caller
from .review_targets import ReviewTarget, capture_target
from .stable_media_capture import require_media_generation

router = APIRouter()
CUES = ActionPair(C.REVIEWS_CUES_EDIT_OWN, C.REVIEWS_CUES_EDIT_OTHER)


class DraftFilterPreview(ReviewWriteModel):
    media_source_id: str | None = None
    stable_version_id: str | None = Field(default=None, min_length=1, max_length=128)
    target_generation: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    audio_track_index: int | None = Field(default=None, ge=0, le=127, strict=True)
    review_id: str | None = Field(default=None, min_length=1)
    source_scan_id: str | None = Field(default=None, min_length=1)
    source_finding_id: int | None = Field(default=None, gt=0, le=MAX_PERSISTED_INTEGER, strict=True)
    community_link_id: str | None = Field(default=None, min_length=1, max_length=128)
    community_filter_id: str | None = Field(default=None, min_length=1, max_length=128)
    source: Literal["audio", "visual"]
    edit_action: EditAction
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float | None = Field(allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_clip(self):
        if self.source_finding_id is not None and self.source_scan_id is None:
            raise ValueError("A finding preview requires its source scan")
        if ((self.community_link_id is None) != (self.community_filter_id is None)
                or (self.community_link_id is not None and self.source_scan_id is not None)):
            raise ValueError("A community preview requires its link and filter instead of a scan")
        if self.end_seconds is not None and self.end_seconds <= self.start_seconds:
            raise ValueError("End time must be after start time")
        return self


@dataclass(frozen=True)
class PreviewClip:
    path: Path
    source_path: str
    start: float
    end: float
    version_ids: tuple[str, ...] = ()
    generation: tuple[int, ...] | None = None


def _target_path(db: Session, target: ReviewTarget) -> Path:
    """Recheck the current mapping and readability of the captured catalog file."""
    try:
        resolved = root.resolve_local_path(db, target.source.path).local_path
        path = Path(resolved).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise root._review_http_error(
            "media_unavailable", "The selected media file is unavailable"
        ) from exc
    if resolved != target.media.resolved_path or not path.is_file() or not os.access(path, os.R_OK):
        raise root._review_http_error("media_unavailable", "The selected media file is unavailable")
    return path


def _matches_finding(finding: ScanFindingRow | None, request: DraftFilterPreview) -> bool:
    return finding is not None and (
        finding.scan_id,
        finding.source,
        finding.start_seconds,
        finding.end_seconds,
        finding.edit_action,
    ) == (
        request.source_scan_id,
        request.source,
        request.start_seconds,
        request.end_seconds,
        request.edit_action,
    )


def _saved_community_sources(
    db: Session, request: DraftFilterPreview
) -> list[ReviewCommunitySourceRow]:
    """The snapshots of the review's saved community filters that exactly match the clip."""
    if request.source_scan_id is not None:
        return []
    row = db.get(SavedReviewRow, request.review_id)
    clip = (request.source, request.edit_action, request.start_seconds, request.end_seconds)
    return [
        db.get(ReviewCommunitySourceRow, (row.id, saved.community_source_id))
        for saved in row.filters
        if saved.community_source_id is not None
        and (saved.source, saved.edit_action, saved.start_seconds, saved.end_seconds) == clip
    ]


def _matches_saved_community_filter(db: Session, request: DraftFilterPreview) -> bool:
    """Whether the clip is exactly one of the review's saved community filters.

    Community filters have no source scan, so they preview from the review's media
    file; an unchanged one is saved evidence, like an unchanged scan finding."""
    return bool(_saved_community_sources(db, request))


def _timeline_versions(db: Session, request: DraftFilterPreview, review_id: str) -> set[str]:
    """The local versions whose timeline a saved review's scan-less clip is placed on.

    The review's bound version pins every filter; a saved community filter's times are
    on the local version its master link was aligned to. A matched community filter
    whose version is unknown (detached link, unbound review) cannot be verified."""
    bound = review_version_id(db, review_id)
    versions = {bound} if bound is not None else set()
    for source in _saved_community_sources(db, request):
        version = source.link.media_version_id if source.link is not None else bound
        if version is None:
            raise _target_changed()
        versions.add(version)
    return versions


def _target_changed():
    return root._review_http_error("target_changed", "The selected version has changed", 409)


def _draft_community(caller: ReviewAccess, target: ReviewTarget, request: DraftFilterPreview):
    """Bind a draft's community filter to the selected version and recognize its original clip."""
    if request.community_link_id is None:
        return None, False
    from scanner.persistence.repository.review_community_drafts import project_draft

    from .community_master_access import authorize_link, version_locations

    db = caller.db
    link = authorize_link(db, caller.access, request.community_link_id, C.COMMUNITY_SEARCH)
    source = target.source
    locations = version_locations(db, link.media_version_id)
    if (link.alignment_state == "required" or link.link_state == "replaced"
            or not any(
                (item.server_id, item.jellyfin_id, locator.media_source_id)
                == (source.server_id, source.item_id, source.media_source_id or "")
                for locator, item in locations
            )):
        raise not_found()
    _, filters = project_draft(db, link)
    original = next(
        (row for row in filters if row.community_filter_id == request.community_filter_id), None
    )
    if original is None:
        raise not_found()
    return link.media_version_id, (
        original.source, original.edit_action, original.start_seconds, original.end_seconds
    ) == (request.source, request.edit_action, request.start_seconds, request.end_seconds)


def _authorize_saved_draft(
    caller: ReviewAccess,
    request: DraftFilterPreview,
    target: ReviewTarget,
    resource: ResourceRef,
) -> None:
    """Allow saved evidence with the review's preview grant; other clips need cue editing."""
    _saved_preview_media(caller.db, request.review_id, target)
    if request.community_link_id is not None:
        _, original = _draft_community(caller, target, request)
        if not original:
            require(caller.access, CUES, resource)
        return
    finding = (
        caller.db.get(ScanFindingRow, request.source_finding_id)
        if (request.source_finding_id is not None)
        else None
    )
    if not _matches_finding(finding, request) and not _matches_saved_community_filter(
        caller.db, request
    ):
        require(caller.access, CUES, resource)


def _saved_preview_media(db, review_id, target):
    """Require the draft target to match its saved review before reading any media bytes."""
    row = db.get(SavedReviewRow, review_id)
    source = target.source
    if row is None or (
        row.media_item.server_id,
        row.media_item.jellyfin_id,
        row.media_source_id,
    ) != (source.server_id, source.item_id, source.media_source_id or ""):
        raise not_found()
    return row.media_item


def _preview_clip(
    caller: ReviewAccess, target: ReviewTarget, request: DraftFilterPreview, review_id: str | None
) -> PreviewClip:
    """Validate request evidence against the current target under the authority transaction."""
    db = caller.db
    source = target.source
    if review_id is not None:
        media = _saved_preview_media(db, review_id, target)
    else:
        media = (
            db.query(MediaItemRow)
            .filter_by(server_id=source.server_id, jellyfin_id=source.item_id)
            .one_or_none()
        )

    path = _target_path(db, target)
    versions = {request.stable_version_id} if request.stable_version_id is not None else set()
    draft_version, original_community_clip = _draft_community(caller, target, request)
    if draft_version is not None:
        versions.add(draft_version)
    if review_id is not None and request.source_scan_id is None:
        # Without a source scan, nothing else ties the current file to the filter's timeline.
        versions |= _timeline_versions(db, request, review_id)
    if request.stable_version_id is not None:
        version = get_version(db, request.stable_version_id)
        if (
            version is None
            or not (version.sampled_md5 or version.content_sha256)
            or find_title_id(db, server_id=source.server_id, jellyfin_id=source.item_id)
            != version.title_id
        ):
            raise not_found()
    duration = source.duration_seconds
    edited = not original_community_clip
    generation = None
    if request.source_scan_id is not None:
        scan = db.get(ScanRow, request.source_scan_id)
        if (
            media is None
            or scan is None
            or not repository._scan_is_available(
                db, scan, media_item_id=media.id, media_source_id=source.media_source_id
            )
        ):
            raise not_found()
        role = "audio" if request.source == "audio" else "video"
        if not getattr(scan, f"scan_{role}"):
            raise not_found()
        association = verified_target(db, scan, media.id, source.media_source_id)
        validate_target_role(association, role)
        if association is None and repository._resolved_scan_path(scan) != path:
            generation = file_generation(str(path))
        matches = (
            matches_target(association, path)
            if association is not None
            else repository._scan_matches_physical_version(scan, path)
        )
        if not matches:
            raise not_found()
        if request.source_finding_id is not None:
            finding = db.get(ScanFindingRow, request.source_finding_id)
            if finding is None or finding.scan_id != scan.id or finding.source != request.source:
                raise not_found()
            edited = not _matches_finding(finding, request)
        source_duration = review_duration(scan, media.id, source.media_source_id)
        if source_duration is not None:
            duration = min(duration, source_duration) if duration is not None else source_duration

    # Own preview grants access to evidence as-is. Previewing an arbitrary range
    # also needs the same capability as authoring a cue on that media; saved reviews
    # apply the same rule in _authorize_saved_draft.
    if review_id is None and edited and C.REVIEWS_CUES_EDIT_OWN not in caller.access.capabilities:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)

    end = request.end_seconds if request.end_seconds is not None else request.start_seconds + 1.0
    if duration is not None:
        end = min(end, duration)
    if end <= request.start_seconds:
        raise ValueError("The filter does not have a range within the media")
    return PreviewClip(
        path, source.path, request.start_seconds, end, tuple(sorted(versions)), generation
    )


def _verify_preview_file(
    db: Session, clip: PreviewClip, target_generation: str | None, proof: ReviewMediaProof | None
) -> None:
    """Keep the same file throughout playback, including lightweight alignment previews."""
    if clip.generation is not None:
        try:
            require_media_generation(db, clip.source_path, clip.path, clip.generation)
        except ScanVersionChanged as exc:
            raise _target_changed() from exc
    if target_generation:
        try:
            matches = generation_token(file_generation(str(clip.path))) == target_generation
        except ScanVersionChanged:
            matches = False
        if not matches:
            raise _target_changed()
    with use_review_media_proof(db, proof):
        for version_id in clip.version_ids:
            if not version_file_matches(get_version(db, version_id), clip.path, db=db):
                raise _target_changed()


@router.post("/api/library-items/{item_id}/reviews/preview")
def api_draft_filter_preview(
    item_id: str, request: DraftFilterPreview, caller: ReviewAccess = Depends(review_caller)
):
    """Preview one filter without creating or modifying a review or scan."""
    if request.review_id is not None:
        caller.visible(request.review_id)
    target = capture_target(
        caller,
        ReviewTargetRequest(
            source="jellyfin", item_id=item_id, media_source_id=request.media_source_id
        ),
    )
    scan_ids = [request.source_scan_id] if request.source_scan_id is not None else []
    try:
        if request.review_id is None:
            clip = caller.create(
                target.media,
                lambda _db: _preview_clip(caller, target, request, None),
                capability=C.REVIEWS_PREVIEW_OWN,
                scan_ids=scan_ids,
                creating=False,
            )
        else:
            clip = caller.run(
                request.review_id,
                PREVIEW,
                lambda _db, review_id: _preview_clip(caller, target, request, review_id),
                scan_ids=scan_ids,
                authorize=lambda resource: _authorize_saved_draft(
                    caller, request, target, resource
                ),
                prepared=target if scan_ids else None,
                capture_review_media=bool(scan_ids),
                store_prepared=False,
            )
    except ValueError as exc:
        raise root._review_mutation_error(exc) from exc

    # FFmpeg runs after releasing the authority transaction's SQLite write lock. The clip
    # shows what the filter's action does, whatever its source: a mute or a skip.
    mute = request.edit_action == EditAction.MUTE
    render = repository.render_mute_preview if mute else repository.render_video_preview
    try:
        proof = capture_media_proof(clip.path) if clip.version_ids else None
        _verify_preview_file(caller.db, clip, request.target_generation, proof)
        if request.audio_track_index is None:
            content = render(clip.path, clip.start, clip.end)
        else:
            audio = select_target_audio(clip.path, request.audio_track_index)
            content = render(
                clip.path, clip.start, clip.end, audio_stream_index=audio.track.stream_index
            )
        _verify_preview_file(caller.db, clip, request.target_generation, proof)
    except (OSError, RuntimeError, ValueError) as exc:
        raise root._review_http_error(
            "review_preview_failed", "Unable to render the filter preview", 422
        ) from exc
    return Response(
        content=content,
        media_type="audio/mpeg" if mute else "video/mp4",
        headers={"cache-control": "no-store"},
    )
