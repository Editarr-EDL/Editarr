"""Resolve effective review inputs and render a selected subtitle deterministically."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scanner import repository
from scanner.db import SavedReviewRow
from scanner.formats.subtitle_redaction import SubtitleMute, SubtitleRedaction, redact_subtitles
from scanner.integrations.jellyfin_subtitles import (
    JellyfinSubtitle,
    discover_jellyfin_subtitles,
    subtitle_id,
)
from scanner.modeling.model_subtitles import (
    PublishedSubtitle,
    SubtitleSource,
    SubtitleSources,
    SubtitleSourcesRequest,
)
from scanner.models import EditAction, McfCategoryFamily, McfChannel
from scanner.runtime.subtitle_inputs import read_subtitle
from scanner.runtime.subtitle_sidecars import discover_publications, file_fingerprint, media_slot
from scanner.runtime.subtitle_transcript import (
    ReviewedAudio,
    cached_segments,
    reviewed_audio_sources,
    transcript_document,
)

from .repository_paths import resolve_local_path
from .review_community_subtitles import community_subtitle_mutes
from .review_export_target import _review_export_scans
from .review_policy import prepare_review_policy, require_policy_evidence, require_policy_revision
from .review_policy_evidence import policy_export_filters
from .review_version_evidence import review_file_matches


class SubtitleError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class ReviewSubtitleEvidence:
    version: str
    media: Path
    fingerprint: tuple[int, ...]
    mutes: tuple[SubtitleMute, ...]
    audio: tuple[ReviewedAudio, ...]


@dataclass(frozen=True)
class SubtitleCatalog:
    evidence: ReviewSubtitleEvidence
    tracks: tuple[JellyfinSubtitle, ...]
    audio: ReviewedAudio | None
    transcript: list[dict] | None
    response: SubtitleSources


@dataclass(frozen=True)
class RenderedSubtitle:
    source: SubtitleSource
    redaction: SubtitleRedaction
    content: bytes
    token: str
    input_path: Path | None
    input_fingerprint: tuple[int, ...] | None


def _require_unambiguous_name(db, target, actual: Path) -> None:
    # The selected sidecar also attaches to a shorter sibling stem ending at a dot.
    item = target.source.catalog_item
    for value in item.versions:
        if value.id == target.source.media_source_id or not value.path:
            continue
        try:
            other = Path(resolve_local_path(db, value.path).local_path).resolve()
        except (OSError, ValueError, RuntimeError):
            continue
        stem, other_stem = actual.stem.casefold(), other.stem.casefold()
        if (
            other != actual
            and other.parent == actual.parent
            and (stem == other_stem or stem.startswith(other_stem + "."))
        ):
            raise SubtitleError(
                "subtitle_version_ambiguous",
                "Jellyfin would attach the subtitle to more than one version in this folder",
            )


def review_subtitle_evidence(
    db,
    review_id: str,
    target,
    request: SubtitleSourcesRequest,
) -> ReviewSubtitleEvidence:
    """Recheck exact physical media, saved revision and enforced owner policy."""
    review = db.get(SavedReviewRow, review_id)
    repository._check_review_revision(db, review, request.revision)
    policy = prepare_review_policy(db, review)
    require_policy_revision(policy, request.policy_revision, request.policy_owner_id)
    require_policy_evidence(policy)
    target_media = Path(target.media.resolved_path).resolve(strict=True)
    if review_file_matches(db, review.id, target_media) is False:
        raise SubtitleError("subtitle_media_changed", "The original review version is unavailable")
    if review.memberships:
        memberships, scans, media = _review_export_scans(db, review)
        if media != target_media:
            raise SubtitleError("subtitle_media_changed", "The selected media version changed")
    else:
        # A freshly saved or older manual-only review has an authorized version even
        # without separate scan evidence. The transcript still needs a proven audio scan.
        memberships, scans, media = (), {}, target_media
    if review.media_source_id != (target.source.media_source_id or ""):
        raise SubtitleError("subtitle_media_changed", "The selected media version changed")
    _require_unambiguous_name(db, target, media)
    mutes = tuple(
        SubtitleMute(item.start_seconds, item.end_seconds, item.label)
        for item in policy_export_filters(policy, duration_seconds=target.source.duration_seconds)
        if item.selected
        and item.source == "audio"
        and item.category == McfCategoryFamily.LANGUAGE
        and item.edit_action == EditAction.MUTE
        and item.mcf_channel != McfChannel.VIDEO
        and item.end_seconds is not None
        and item.end_seconds > item.start_seconds
        and item.label
    ) + community_subtitle_mutes(db, review, target.source.duration_seconds).mutes
    return ReviewSubtitleEvidence(
        target.source.media_source_id or "",
        media,
        file_fingerprint(media),
        mutes,
        tuple(reviewed_audio_sources(db, memberships, scans, media)),
    )


def _transcript_source(
    evidence: ReviewSubtitleEvidence,
    audio: ReviewedAudio,
    published: list[PublishedSubtitle],
) -> JellyfinSubtitle:
    from scanner.runtime.subtitle_sidecars import publication_path

    source = SubtitleSource(
        id=subtitle_id(evidence.version, "transcript", str(audio.selection.track.stream_index)),
        label=f"Audio transcript · {audio.source.label}",
        language=audio.source.language,
        format="srt",
        kind="transcript",
    )
    filename = publication_path(evidence.media, source).name
    if any(row.filename == filename for row in published):
        source = source.model_copy(update={"published_filename": filename})
    return JellyfinSubtitle(source)


def subtitle_catalog(
    db, evidence: ReviewSubtitleEvidence, target, requested_audio
) -> SubtitleCatalog:
    """Find supported tracks and expose transcript fallback only when none are suitable."""
    audio = (
        next((entry for entry in evidence.audio if entry.source.id == requested_audio), None)
        if requested_audio is not None
        else evidence.audio[0]
        if len(evidence.audio) == 1
        else None
    )
    if requested_audio is not None and audio is None:
        raise SubtitleError(
            "subtitle_audio_changed", "The reviewed audio tracks changed. Choose a track again."
        )
    media = evidence.media
    published = discover_publications(media, media_slot(media, evidence.version))
    tracks = discover_jellyfin_subtitles(
        db,
        item=target.source.catalog_item,
        version=target.source.media_source_id,
        server_path=target.source.path,
        media=media,
        audio_language=audio.source.language if audio else None,
        published={row.filename for row in published},
    )
    fallback, transcript = None, None
    if not any(track.source.supported for track in tracks):
        if audio is None and len(evidence.audio) > 1:
            fallback = "choose_audio"
        else:
            transcript = cached_segments(audio) if audio else None
            fallback = "available" if transcript is not None else "scan_required"
            if transcript is not None and audio is not None:
                tracks.append(_transcript_source(evidence, audio, published))
    response = SubtitleSources(
        sources=[item.source for item in tracks],
        fallback=fallback,
        audio_sources=[entry.source for entry in evidence.audio],
        audio_source_id=audio.source.id if audio else None,
        published_subtitles=published,
    )
    return SubtitleCatalog(evidence, tuple(tracks), audio, transcript, response)


def _stable_input(path: Path | None) -> tuple[int, ...] | None:
    return file_fingerprint(path) if path is not None else None


def render_subtitle(
    review_id: str,
    request: SubtitleSourcesRequest,
    catalog: SubtitleCatalog,
    source_id: str,
) -> RenderedSubtitle:
    """Render outside SQLite's writer lock and bind the preview to exact inputs."""
    evidence = catalog.evidence
    track = next((found for found in catalog.tracks if found.source.id == source_id), None)
    if not track or not track.source.supported:
        raise SubtitleError("subtitle_source_unavailable", "Choose an available subtitle source")
    input_path = track.path
    try:
        before = _stable_input(input_path)
        if track.source.kind == "transcript":
            assert catalog.audio is not None and catalog.transcript is not None
            document = transcript_document(evidence.media, catalog.audio, catalog.transcript)
        else:
            document = read_subtitle(evidence.media, track)
        if (
            file_fingerprint(evidence.media) != evidence.fingerprint
            or _stable_input(input_path) != before
        ):
            raise SubtitleError("subtitle_source_changed", "The subtitle source changed")
        redaction = redact_subtitles(document, evidence.mutes)
        content = document.render(redaction.replacements)
    except SubtitleError:
        raise
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SubtitleError(
            "subtitle_source_unavailable", "The subtitle source could not be read safely"
        ) from exc

    metadata = json.dumps(
        {
            "review": review_id,
            "revision": request.revision,
            "policy": request.policy_revision,
            "owner": request.policy_owner_id,
            "version": evidence.version,
            "file": evidence.fingerprint,
            "audio": catalog.response.audio_source_id,
            "source": track.source.model_dump(exclude={"published_filename", "recommended"}),
        },
        sort_keys=True,
        ensure_ascii=True,
    ).encode()
    digest = hashlib.sha256()
    digest.update(metadata)
    digest.update(document.text.encode(document.encoding))
    digest.update(content)
    return RenderedSubtitle(
        track.source, redaction, content, digest.hexdigest(), input_path, before
    )


def require_same_subtitle_input(
    saved_request: ReviewSubtitleEvidence,
    current_request: ReviewSubtitleEvidence,
    output: RenderedSubtitle,
) -> None:
    """Stop publication if an input changed after producing the authorized preview."""
    try:
        unchanged = (
            saved_request == current_request
            and file_fingerprint(current_request.media) == current_request.fingerprint
            and _stable_input(output.input_path) == output.input_fingerprint
        )
    except (OSError, ValueError):
        unchanged = False
    if not unchanged:
        raise SubtitleError(
            "subtitle_source_changed", "The subtitle source changed. Preview it before publishing."
        )
