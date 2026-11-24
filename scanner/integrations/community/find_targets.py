"""Capture the master-set Find keys for one local target (local API §2).

The backend resolves the target, measures fingerprints within the existing budgets
and derives provider IDs, title, year and runtime; the browser never supplies keys.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from scanner.integrations.jellyfin import JellyfinClient, JellyfinError, JellyfinNotFound
from scanner.modeling.community_match_keys import FileKey, TitleYearKey
from scanner.modeling.community_work_identity import (
    WorkIdentity,
    WorkIdentityRequired,
    title_key,
    work_identity,
)
from scanner.modeling.media_identity import MediaIdentity
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.pipeline.audio_tracks import AudioSelectionError, _file_state, probe_audio_tracks
from scanner.pipeline.frames import probe_video_duration
from scanner.repository import get_jellyfin_credentials, resolve_local_path
from scanner.runtime.media_fingerprints import SampledMediaFingerprint, sampled_media_fingerprint

from .fingerprints import FileChangedError
from .match_key_capture import capture_file_keys
from .transport import CommunityFailure


class MissingTarget(CommunityFailure):
    """A well-formed target has no local row or is confirmed absent by its provider."""

    def __init__(self):
        super().__init__("invalid_target")


class FindTarget(BaseModel):
    """`{source, item_id, media_source_id}` as the local API receives it."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["jellyfin"]
    item_id: str = Field(min_length=1, max_length=4096)
    media_source_id: str | None = Field(default=None, max_length=128)


@dataclass(frozen=True)
class ResolvedTarget:
    path: Path
    media: MediaIdentity
    title: str
    year: int | None
    duration_seconds: float | None


@dataclass(frozen=True)
class FindKeys:
    """The keys a `/v1/match` query can carry for one local file."""

    file_keys: tuple[FileKey, ...]
    work: WorkIdentity | None
    title_year: TitleYearKey | None
    runtime_ms: int | None


@dataclass(frozen=True)
class CapturedFind:
    path: Path
    state: tuple
    keys: FindKeys
    sampled: SampledMediaFingerprint
    guard: str
    warnings: tuple[str, ...]

    def check(self, should_cancel=None):
        """Refuse a cancelled request or a file that changed since capture."""
        if should_cancel and should_cancel():
            raise CommunityFailure("lookup_cancelled")
        try:
            if _file_state(self.path) != self.state:
                raise CommunityFailure("file_changed")
        except (OSError, AudioSelectionError) as error:
            raise CommunityFailure("file_changed") from error


def resolve_find_target(db: Session, target: FindTarget, *, authorize=None) -> ResolvedTarget:
    """Resolve one provider version and its local file without writing any rows."""
    credentials = get_jellyfin_credentials(db)
    if not credentials:
        raise CommunityFailure("target_unavailable", retryable=True)
    try:
        with JellyfinClient(*credentials) as client:
            item = client.item(target.item_id)
    except JellyfinNotFound as error:
        raise MissingTarget() from error
    except JellyfinError as error:
        raise CommunityFailure("target_unavailable", retryable=True) from error
    version = next((value for value in item.versions if value.id == target.media_source_id), None)
    conflict = None
    if target.media_source_id and version is None:
        conflict = "target_changed"
    elif version is None and len(item.versions) > 1:
        conflict = "version_required"
    version = version or (item.versions[0] if item.versions else None)
    source_path = version.path if version else item.path
    media = (version.media_identity if version else None) or item.media_identity
    try:
        path = (
            Path(resolve_local_path(db, source_path).local_path).resolve() if source_path else None
        )
        if authorize is not None:
            from scanner.persistence.repository.scan_jobs import ScanMedia

            resolved = str(path) if path else ""
            authorize(ScanMedia(credentials[0], target.item_id, item.library_id, resolved))
        if conflict is not None:
            raise CommunityFailure(conflict)
        if media is None:
            raise CommunityFailure("unsupported_target")
        if path is None:
            raise CommunityFailure("target_unavailable")
        path = path.resolve(strict=True)
        _file_state(path)
    except (OSError, ValueError) as error:
        raise CommunityFailure("target_unavailable") from error
    return ResolvedTarget(path, media, item.name, item.year, item.duration_seconds)


def _runtime_ms(path: Path, fallback: float | None, should_cancel, warnings: list[str]):
    try:
        seconds = probe_video_duration(path, should_cancel)
    except (OSError, RuntimeError, ValueError):
        warnings.append("measured_duration_unavailable")
        seconds = fallback
    if not seconds or seconds <= 0:
        return None
    milliseconds = int(round(seconds * 1000))
    return milliseconds if 1 <= milliseconds <= 86_400_000 else None


def capture_find_keys(
    db: Session,
    target: FindTarget,
    *,
    audio_selection: AudioSelection | None = None,
    should_cancel: Callable[[], bool] | None = None,
    authorize=None,
) -> CapturedFind:
    """Measure every available key for the target's current file generation.

    `guard` binds the target, the file generation and the keys; a Find result is only
    reused while it is unchanged.
    """
    resolved = resolve_find_target(db, target, authorize=authorize)
    path, warnings = resolved.path, []
    before = _file_state(path)
    selection = audio_selection.selection() if audio_selection else None
    if selection is not None:
        try:
            probe_audio_tracks(path).revalidate(selection)
        except (OSError, ValueError) as error:
            raise CommunityFailure("audio_selection_stale") from error
    file_keys = capture_file_keys(
        path, audio_selection=selection, should_cancel=should_cancel, warnings=warnings
    )
    try:
        sampled = sampled_media_fingerprint(path)
    except FileChangedError as error:
        raise CommunityFailure("file_changed") from error
    try:
        work = work_identity(resolved.media, title=resolved.title, year=resolved.year)
    except WorkIdentityRequired:
        work = None
        warnings.append("work_identity_unavailable")
    keys = FindKeys(
        file_keys,
        work,
        title_key(resolved.title, resolved.year),
        _runtime_ms(path, resolved.duration_seconds, should_cancel, warnings),
    )
    captured = CapturedFind(path, before, keys, sampled, "", tuple(warnings))
    captured.check(should_cancel)
    guard = hashlib.sha256(
        json.dumps(
            [target.model_dump(), str(path), before, repr(keys)],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return CapturedFind(path, before, keys, sampled, guard, tuple(warnings))
