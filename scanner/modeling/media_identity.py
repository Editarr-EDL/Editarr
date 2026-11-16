"""Typed public media identity derived only from explicit provider metadata."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

EntityType = Literal["movie", "series", "episode"]


@dataclass(frozen=True)
class ProviderIdentifier:
    provider: str
    entity_type: EntityType
    value: str
    provenance: str

    def __post_init__(self) -> None:
        if self.provider not in {"tmdb", "tvdb"}:
            raise ValueError("Unsupported provider")
        if (
            not self.value
            or len(self.value) > 64
            or not self.value.isascii()
            or not self.value.isalnum()
        ):
            raise ValueError("Provider ID must be a bounded public identifier")
        if not self.provenance or len(self.provenance) > 64:
            raise ValueError("Provider ID requires bounded provenance")


@dataclass(frozen=True)
class EpisodeNumber:
    scheme: Literal["aired", "absolute", "dvd", "anime"]
    season: int | None
    number: int

    def __post_init__(self) -> None:
        if self.number < 0 or (self.season is not None and self.season < 0):
            raise ValueError("Episode numbering must be nonnegative")
        if self.scheme in {"aired", "dvd"} and self.season is None:
            raise ValueError("Season required for aired or DVD numbering")


@dataclass(frozen=True)
class DurationEvidence:
    seconds: float
    source: Literal["file_probe", "jellyfin_ticks"]
    precision_seconds: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.seconds) or self.seconds <= 0:
            raise ValueError("Duration must be finite and positive")


@dataclass(frozen=True)
class AudioEvidence:
    language: str | None
    role: Literal["main", "commentary", "descriptive", "unknown"]
    codec: str | None = None
    channels: int | None = None


@dataclass(frozen=True)
class ReleaseEvidence:
    scene_name: str | None = None
    group: str | None = None
    edition: str | None = None
    quality: str | None = None
    size_bytes: int | None = None
    duration: DurationEvidence | None = None
    audio: AudioEvidence | None = None

    def __post_init__(self) -> None:
        for text in (self.scene_name, self.group, self.edition, self.quality):
            if text is not None and (len(text) > 256 or "/" in text or "\\" in text):
                raise ValueError("Release labels cannot contain local paths")


def known_release_conflicts(
    left: ReleaseEvidence, right: ReleaseEvidence, *, duration_tolerance_seconds: float
) -> tuple[str, ...]:
    """Report explicit file/audio conflicts independent of scene/group name."""
    if not math.isfinite(duration_tolerance_seconds) or duration_tolerance_seconds < 0:
        raise ValueError("Duration tolerance must be finite and nonnegative")
    conflicts: list[str] = []
    if (
        left.duration is not None
        and right.duration is not None
        and left.duration.source == right.duration.source == "file_probe"
        and abs(left.duration.seconds - right.duration.seconds) > duration_tolerance_seconds
    ):
        conflicts.append("measured_duration")
    if left.audio is not None and right.audio is not None:
        if (
            left.audio.language
            and right.audio.language
            and left.audio.language != right.audio.language
        ):
            conflicts.append("audio_language")
        if (
            left.audio.role != "unknown"
            and right.audio.role != "unknown"
            and left.audio.role != right.audio.role
        ):
            conflicts.append("audio_role")
    return tuple(conflicts)


@dataclass(frozen=True)
class MediaIdentity:
    entity_type: EntityType
    identifiers: tuple[ProviderIdentifier, ...]
    series_identifiers: tuple[ProviderIdentifier, ...] = ()
    episode_numbers: tuple[EpisodeNumber, ...] = ()
    episode_span: tuple[EpisodeNumber, ...] = ()
    release: ReleaseEvidence = ReleaseEvidence()
    input_requirement: str | None = None


def provider_identifiers(
    entity_type: EntityType,
    values: dict[str, object],
    *,
    source: str,
) -> tuple[ProviderIdentifier, ...]:
    """Keep distinct typed aliases without inferring episode IDs from series IDs."""
    keys: dict[str, EntityType] = {
        "tmdb": "movie" if entity_type == "movie" else "series",
        "tvdb": "series",
    }
    identifiers: list[ProviderIdentifier] = []
    for provider, kind in keys.items():
        value = values.get(provider)
        if kind != entity_type:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        text = str(value).strip()
        if text and text != "0" and text.isascii() and text.isalnum() and len(text) <= 64:
            identifiers.append(ProviderIdentifier(provider, kind, text, source))
    return tuple(identifiers)


def _values(payload: object, names: dict[str, str]) -> dict[str, object]:
    return {
        provider: payload[key]
        for provider, key in names.items()
        if isinstance(payload, dict) and key in payload
    }


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:256] if text and "/" not in text and "\\" not in text else None


def _identity(
    kind: EntityType,
    aliases: tuple[ProviderIdentifier, ...],
    *,
    series: tuple[ProviderIdentifier, ...] = (),
    numbers: tuple[EpisodeNumber, ...] = (),
    span: tuple[EpisodeNumber, ...] = (),
    release: ReleaseEvidence = ReleaseEvidence(),
) -> MediaIdentity:
    requirement = None
    if kind == "episode" and not (aliases or (series and numbers)):
        requirement = "Confirm an episode provider ID, or a typed series ID and episode numbering"
    elif kind != "episode" and not aliases:
        requirement = f"Confirm a typed {kind} provider ID before sharing or matching"
    return MediaIdentity(kind, aliases, series, numbers, span, release, requirement)


def normalize_jellyfin(
    item: dict[str, object],
    *,
    series: dict[str, object] | None = None,
    source: dict[str, object] | None = None,
    measured_duration: float | None = None,
    audio: AudioEvidence | None = None,
) -> MediaIdentity:
    """Do not reinterpret Jellyfin's series ProviderIds as episode IDs."""
    kind = {"Movie": "movie", "Series": "series", "Episode": "episode"}.get(item.get("Type"))
    if kind is None:
        raise ValueError("Expected a movie, series or episode")
    fields = {"tmdb": "Tmdb", "tvdb": "Tvdb"}
    aliases = provider_identifiers(
        kind,
        _values(item.get("ProviderIds"), fields),
        source="jellyfin.item",
    )
    series_ids = (
        provider_identifiers(
            "series",
            _values(series.get("ProviderIds") if series else None, fields),
            source="jellyfin.series",
        )
        if kind == "episode"
        and series is not None
        and series.get("Id") is not None
        and item.get("SeriesId") is not None
        and str(series["Id"]) == str(item["SeriesId"])
        else ()
    )
    season, number = _number(item.get("ParentIndexNumber")), _number(item.get("IndexNumber"))
    numbers = (
        (EpisodeNumber("aired", season, number),)
        if season is not None and number is not None
        else ()
    )
    source = source or {}
    ticks = source.get("RunTimeTicks") or item.get("RunTimeTicks")
    duration = (
        DurationEvidence(measured_duration, "file_probe")
        if measured_duration is not None
        else DurationEvidence(ticks / 10_000_000, "jellyfin_ticks", 0.0000001)
        if isinstance(ticks, int) and ticks > 0
        else None
    )
    return _identity(
        kind,
        aliases,
        series=series_ids,
        numbers=numbers,
        release=ReleaseEvidence(
            _text(source.get("SceneName")),
            _text(source.get("ReleaseGroup")),
            _text(source.get("Edition")),
            None,
            _number(source.get("Size")),
            duration,
            audio,
        ),
    )


def normalize_manual(
    kind: EntityType,
    identifiers: tuple[ProviderIdentifier, ...] = (),
    *,
    series_identifiers: tuple[ProviderIdentifier, ...] = (),
    episode_numbers: tuple[EpisodeNumber, ...] = (),
    episode_span: tuple[EpisodeNumber, ...] = (),
    release: ReleaseEvidence = ReleaseEvidence(),
    confirmed: bool = False,
) -> MediaIdentity:
    """Filename-only candidates require confirmation of typed public identity."""
    if not confirmed:
        return MediaIdentity(
            kind,
            (),
            release=release,
            input_requirement="Confirm typed provider IDs for this file before sharing or matching",
        )
    if any(alias.entity_type != kind for alias in identifiers) or any(
        alias.entity_type != "series" for alias in series_identifiers
    ):
        raise ValueError("Provider ID has the wrong entity type")
    if any(alias.provenance != "user_confirmed" for alias in (*identifiers, *series_identifiers)):
        raise ValueError("Manual provider IDs must be user-confirmed")
    return _identity(
        kind,
        identifiers,
        series=series_identifiers,
        numbers=episode_numbers,
        span=episode_span,
        release=release,
    )
