"""Resolve effective review inputs and render a selected subtitle deterministically."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from scanner import repository
from scanner.db import JellyfinAccountSyncRow, SavedReviewRow
from scanner.formats.subtitle_redaction import SubtitleMute, SubtitleRedaction, redact_subtitles
from scanner.integrations.jellyfin_subtitles import (
    JellyfinSubtitle,
    discover_jellyfin_subtitles,
    subtitle_id,
)
from scanner.modeling.model_subtitles import (
    SubtitleSource,
    SubtitleSources,
    SubtitleSourcesRequest,
)
from scanner.models import McfCategoryFamily, McfChannel
from scanner.runtime.subtitle_inputs import read_subtitle
from scanner.runtime.subtitle_publication_names import (
    NamedSubtitlePublication,
    SubtitleNameError,
    named_publication_path,
    named_track_title,
    subtitle_publisher_id,
)
from scanner.runtime.subtitle_sidecars import (
    SubtitleConflict,
    check_named_destination,
    discover_publications_and_owned_sources,
    file_fingerprint,
    media_slot,
)
from scanner.runtime.subtitle_transcript import (
    ReviewedAudio,
    cached_segments,
    reviewed_audio_sources,
    transcript_document,
)

from .media_identity_store import review_version_id
from .repository_paths import resolve_local_path
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
    stable_version: str | None


@dataclass(frozen=True)
class SubtitleCatalog:
    evidence: ReviewSubtitleEvidence
    tracks: tuple[JellyfinSubtitle, ...]
    audio: ReviewedAudio | None
    transcript: list[dict] | None
    response: SubtitleSources
    publication: NamedSubtitlePublication | None
    publication_error: SubtitleNameError | None


@dataclass(frozen=True)
class RenderedSubtitle:
    source: SubtitleSource
    redaction: SubtitleRedaction
    content: bytes
    token: str | None
    input_path: Path | None
    input_fingerprint: tuple[int, ...] | None
    destination: Path | None
    publication_error: SubtitleNameError | SubtitleConflict | None


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


def subtitle_mutes(policy, duration_seconds=None) -> tuple[SubtitleMute, ...]:
    """The words filtered subtitles redact: one rule for every filter, whatever its source.

    Each enabled audio-channel language filter with a word or phrase (its label) redacts
    that expression, whether it mutes or skips: subtitles keep the original timeline, so
    a cue overlapping a skipped range is still shown. A filter without a word changes
    nothing.
    """
    return tuple(
        SubtitleMute(item.start_seconds, item.end_seconds, item.label)
        for item in policy_export_filters(policy, duration_seconds=duration_seconds)
        if item.selected
        and item.source == "audio"
        and item.category == McfCategoryFamily.LANGUAGE
        and item.mcf_channel != McfChannel.VIDEO
        and item.end_seconds is not None
        and item.end_seconds > item.start_seconds
        and item.label
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
    mutes = subtitle_mutes(policy, target.source.duration_seconds)
    return ReviewSubtitleEvidence(
        target.source.media_source_id or "",
        media,
        file_fingerprint(media),
        mutes,
        tuple(reviewed_audio_sources(db, memberships, scans, media)),
        review_version_id(db, review.id),
    )


def subtitle_publication_intent(
    db, review_id: str, account, evidence: ReviewSubtitleEvidence
) -> NamedSubtitlePublication:
    """Bind a publishable name to the linked caller and exact review version."""
    user = db.get(JellyfinAccountSyncRow, account.user_id)
    if user is None or account.provider is None:
        raise SubtitleNameError(
            "subtitle_title_unavailable",
            "A saved Jellyfin username is required to publish subtitles. "
            "Sign in with Jellyfin and try again.",
        )
    title = named_track_title(user.jellyfin_name, db.get(SavedReviewRow, review_id).name)
    version = (
        evidence.stable_version
        or hashlib.sha256(
            json.dumps([evidence.version, str(evidence.media), evidence.fingerprint]).encode()
        ).hexdigest()
    )
    return NamedSubtitlePublication(
        subtitle_publisher_id(account.local_export_id.value),
        review_id,
        version,
        title,
    )


def _transcript_source(
    evidence: ReviewSubtitleEvidence,
    audio: ReviewedAudio,
) -> JellyfinSubtitle:
    source = SubtitleSource(
        id=subtitle_id(evidence.version, "transcript", str(audio.selection.track.stream_index)),
        label=f"Audio transcript · {audio.source.label}",
        language=audio.source.language,
        format="srt",
        kind="transcript",
    )
    return JellyfinSubtitle(source)


def subtitle_catalog(
    db,
    evidence: ReviewSubtitleEvidence,
    target,
    requested_audio,
    *,
    review_id: str,
    account,
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
    publication, publication_error = None, None
    try:
        publication = subtitle_publication_intent(
            db,
            review_id,
            account,
            evidence,
        )
    except SubtitleNameError as exc:
        publication_error = exc
    published, own_sources = discover_publications_and_owned_sources(
        media,
        media_slot(media, evidence.version),
        viewer=subtitle_publisher_id(account.local_export_id.value),
        publication=publication,
    )
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
                tracks.append(_transcript_source(evidence, audio))
    tracks = [
        replace(
            track,
            source=track.source.model_copy(
                update={
                    "published_filename": own_sources.get(track.source.id),
                }
            ),
        )
        for track in tracks
    ]
    response = SubtitleSources(
        sources=[item.source for item in tracks],
        fallback=fallback,
        audio_sources=[entry.source for entry in evidence.audio],
        audio_source_id=audio.source.id if audio else None,
        published_subtitles=published,
        track_title=publication.title if publication else None,
        publication_block_reason=str(publication_error) if publication_error else None,
    )
    return SubtitleCatalog(
        evidence,
        tuple(tracks),
        audio,
        transcript,
        response,
        publication,
        publication_error,
    )


def _stable_input(path: Path | None) -> tuple[int, ...] | None:
    return file_fingerprint(path) if path is not None else None


def render_subtitle(
    review_id: str,
    request: SubtitleSourcesRequest,
    catalog: SubtitleCatalog,
    source_id: str,
    get_language_tokens: Callable[[], frozenset[str]],
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

    publication = catalog.publication
    if publication is None:
        return RenderedSubtitle(
            track.source,
            redaction,
            content,
            None,
            input_path,
            before,
            None,
            catalog.publication_error,
        )
    try:
        language_tokens = (
            get_language_tokens()
            if track.source.language or "." in publication.title
            else frozenset()
        )
        destination = named_publication_path(
            evidence.media, track.source, publication, language_tokens
        )
        check_named_destination(
            destination, media_slot(evidence.media, evidence.version), publication, source_id
        )
    except (SubtitleNameError, SubtitleConflict) as exc:
        return RenderedSubtitle(
            track.source, redaction, content, None, input_path, before, None, exc
        )
    except OSError:
        error = SubtitleConflict("Could not verify the subtitle destination")
        return RenderedSubtitle(
            track.source, redaction, content, None, input_path, before, None, error
        )

    metadata = json.dumps(
        {
            "review": review_id,
            "revision": request.revision,
            "policy": request.policy_revision,
            "owner": request.policy_owner_id,
            "version": evidence.version,
            "file": evidence.fingerprint,
            "publication": publication.identity(source_id),
            "track_title": publication.title,
            "filename": destination.name,
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
        track.source,
        redaction,
        content,
        digest.hexdigest(),
        input_path,
        before,
        destination,
        None,
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
