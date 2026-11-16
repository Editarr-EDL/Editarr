"""Deterministic, independently evaluated file and selected-stream compatibility."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from scanner.formats.community.fingerprints import FULL_ALGORITHM, SAMPLED_ALGORITHM
from scanner.formats.community.stream_fingerprints import AUDIO_ALGORITHM, VIDEO_ALGORITHM

from .community_stream_matching import StreamEvidence, compare_streams
from .media_identity import (
    EpisodeNumber,
    MediaIdentity,
    ProviderIdentifier,
    known_release_conflicts,
)

POLICY_VERSION = "community-compatibility-stream-1"
DURATION_TOLERANCE_SECONDS = 2.0
BANDS = (
    "exact_file",
    "strong_fingerprint",
    "release_match",
    "possible_cut",
    "title_only",
    "incompatible",
)
Band = Literal[
    "exact_file",
    "strong_fingerprint",
    "release_match",
    "possible_cut",
    "title_only",
    "incompatible",
]


@dataclass(frozen=True)
class FingerprintEvidence:
    algorithm: str
    digest: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", self.algorithm):
            raise ValueError("Fingerprint algorithm must be a bounded token")
        if not re.fullmatch(r"[a-f0-9]{1,128}", self.digest):
            raise ValueError("Fingerprint digest must be lowercase hex")
        if self.algorithm in (FULL_ALGORITHM, SAMPLED_ALGORITHM) and len(self.digest) != 64:
            raise ValueError("Supported digests require SHA-256 hex")
        if type(self.size_bytes) is not int or not 0 <= self.size_bytes <= (1 << 53) - 1:
            raise ValueError("Comparison sizes must be safe nonnegative JSON integers")


@dataclass(frozen=True)
class MatchEvidence:
    media: MediaIdentity
    fingerprints: tuple[FingerprintEvidence, ...] = ()
    video_stream: StreamEvidence | None = None
    audio_stream: StreamEvidence | None = None

    def __post_init__(self) -> None:
        for stream, wrong_algorithm in (
            (self.video_stream, AUDIO_ALGORITHM),
            (self.audio_stream, VIDEO_ALGORITHM),
        ):
            if stream is not None and stream.algorithm == wrong_algorithm:
                raise ValueError("Stream fingerprint channel mismatch")
        if self.media.entity_type not in {"movie", "series", "episode"}:
            raise ValueError("Unsupported entity type")
        for identifiers, kind in (
            (self.media.identifiers, self.media.entity_type),
            (self.media.series_identifiers, "series"),
        ):
            keys = [(item.provider, item.entity_type) for item in identifiers]
            if len(keys) > 16 or len(set(keys)) != len(keys):
                raise ValueError("Identifier namespaces must be bounded and unique")
            if any(item.entity_type != kind for item in identifiers):
                raise ValueError("Identifier namespace mismatch")
        for numbers in (self.media.episode_numbers, self.media.episode_span):
            if len(numbers) > 32 or len(set(numbers)) != len(numbers):
                raise ValueError("Episode numbers must be bounded and unique")
            for item in numbers:
                if item.scheme not in {"aired", "absolute", "dvd", "anime"}:
                    raise ValueError("Unsupported numbering scheme")
                if any(
                    value is not None
                    and (type(value) is not int or not 0 <= value <= (1 << 53) - 1)
                    for value in (item.season, item.number)
                ):
                    raise ValueError("Numbering requires safe nonnegative JSON integers")
        algorithms = [item.algorithm for item in self.fingerprints]
        if len(algorithms) > 8 or len(set(algorithms)) != len(algorithms):
            raise ValueError("Fingerprint algorithms must be bounded and unique")
        sizes = {
            item.size_bytes
            for item in self.fingerprints
            if item.algorithm in (FULL_ALGORITHM, SAMPLED_ALGORITHM)
        }
        release = self.media.release
        if release.size_bytes is not None:
            if type(release.size_bytes) is not int or not 0 <= release.size_bytes <= (1 << 53) - 1:
                raise ValueError("Comparison sizes must be safe JSON integers")
            sizes.add(release.size_bytes)
        if len(sizes) > 1:
            raise ValueError("Contradictory file size evidence")
        if release.duration is not None:
            if release.duration.source not in {"file_probe", "jellyfin_ticks"}:
                raise ValueError("Unsupported duration source")
            precision = release.duration.precision_seconds
            if precision is not None and (not math.isfinite(precision) or precision < 0):
                raise ValueError("Precision must be finite and nonnegative")
        if release.audio is not None:
            if release.audio.role not in {"main", "commentary", "descriptive", "unknown"}:
                raise ValueError("Unsupported audio role")
            language = release.audio.language
            if language is not None and not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", language):
                raise ValueError("Language must be a bounded language tag")


@dataclass(frozen=True)
class ChannelCompatibility:
    band: Band
    reasons: tuple[str, ...]
    conflicts: tuple[str, ...]
    missing: tuple[str, ...]
    strong_eligible: bool


@dataclass(frozen=True)
class Compatibility:
    policy_version: str
    video: ChannelCompatibility
    audio: ChannelCompatibility
    selected: bool = False


def _ids(
    left: tuple[ProviderIdentifier, ...], right: tuple[ProviderIdentifier, ...]
) -> tuple[bool, bool]:
    first = {(item.provider, item.entity_type): item.value for item in left}
    second = {(item.provider, item.entity_type): item.value for item in right}
    shared = first.keys() & second.keys()
    return any(first[key] == second[key] for key in shared), any(
        first[key] != second[key] for key in shared
    )


def _groups(numbers: tuple[EpisodeNumber, ...]) -> dict[str, tuple[tuple[int | None, int], ...]]:
    return {
        scheme: tuple((item.season, item.number) for item in numbers if item.scheme == scheme)
        for scheme in {item.scheme for item in numbers}
    }


def _work(left: MediaIdentity, right: MediaIdentity) -> tuple[bool, list[str], list[str]]:
    if left.entity_type != right.entity_type:
        return False, ["entity_type"], []
    matched, conflict = _ids(left.identifiers, right.identifiers)
    conflicts = ["work_identity"] if conflict else []
    missing: list[str] = []
    if left.entity_type == "episode":
        series_match, series_conflict = _ids(left.series_identifiers, right.series_identifiers)
        if series_conflict:
            conflicts.append("series_identity")
        numbers, others = _groups(left.episode_numbers), _groups(right.episode_numbers)
        shared = numbers.keys() & others.keys()
        if any(set(numbers[key]) != set(others[key]) for key in shared):
            conflicts.append("episode_numbering")
        if not shared:
            missing.append("episode_numbering")
        matched = matched or bool(series_match and shared and "episode_numbering" not in conflicts)
        spans = _groups(left.episode_span or left.episode_numbers)
        other_spans = _groups(right.episode_span or right.episode_numbers)
        shared_spans = spans.keys() & other_spans.keys()
        if any(spans[key] != other_spans[key] for key in shared_spans):
            conflicts.append("episode_span")
        if not shared_spans:
            missing.append("episode_span")
    identified = all(
        item.identifiers or (item.series_identifiers and item.episode_numbers)
        for item in (left, right)
    )
    if not identified:
        missing.append("work_identity")
    elif not matched and not conflicts:
        missing.append("work_link")
    return matched, conflicts, missing


def _channel(
    band: Band, reasons: list[str], conflicts: list[str], missing: list[str]
) -> ChannelCompatibility:
    return ChannelCompatibility(
        band,
        tuple(sorted(set(reasons))),
        tuple(sorted(set(conflicts))),
        tuple(sorted(set(missing))),
        band in {"exact_file", "strong_fingerprint"},
    )


def classify(target: MatchEvidence, candidate: MatchEvidence) -> Compatibility:
    """Classify each channel without inferring analyzed audio from container bytes."""
    matched, conflicts, missing = _work(target.media, candidate.media)
    reasons = ["work_match"] if matched else []
    left, right = target.media.release, candidate.media.release
    if left.edition is not None and right.edition is not None and left.edition != right.edition:
        conflicts.append("edition")
    release_conflicts = known_release_conflicts(
        left, right, duration_tolerance_seconds=DURATION_TOLERANCE_SECONDS
    )
    if "measured_duration" in release_conflicts:
        conflicts.append("measured_duration")
    measured = all(
        item.duration is not None
        and item.duration.source == "file_probe"
        and (
            item.duration.precision_seconds is None
            or item.duration.precision_seconds <= DURATION_TOLERANCE_SECONDS
        )
        for item in (left, right)
    )
    if measured and "measured_duration" not in conflicts:
        reasons.append("measured_duration_match")
    if not measured:
        missing.append("measured_duration")
    first = {item.algorithm: item for item in target.fingerprints}
    second = {item.algorithm: item for item in candidate.fingerprints}
    equal: set[str] = set()
    different: set[str] = set()
    for algorithm in (FULL_ALGORITHM, SAMPLED_ALGORITHM):
        if algorithm not in first or algorithm not in second:
            continue
        if first[algorithm].digest != second[algorithm].digest:
            different.add(algorithm)
        elif first[algorithm].size_bytes != second[algorithm].size_bytes:
            conflicts.append("fingerprint_size")
        else:
            equal.add(algorithm)
    if FULL_ALGORITHM in different:
        equal.discard(SAMPLED_ALGORITHM)
        reasons.append("full_digest_differs")
    if FULL_ALGORITHM in equal and SAMPLED_ALGORITHM in different:
        conflicts.append("fingerprint_evidence")
    if equal:
        reasons.append("full_digest_match" if FULL_ALGORITHM in equal else "sampled_digest_match")
    else:
        missing.append("matching_fingerprint")
    if any(
        item.algorithm not in (FULL_ALGORITHM, SAMPLED_ALGORITHM)
        for item in (*target.fingerprints, *candidate.fingerprints)
    ):
        reasons.append("unsupported_fingerprint_ignored")
    release_match = bool(
        left.scene_name
        and left.group
        and left.scene_name == right.scene_name
        and left.group == right.group
    )
    if release_match:
        reasons.append("release_name_match")
    else:
        missing.append("release_name")
    if target.media.entity_type == "series":
        missing.append("physical_episode_or_movie")
    if conflicts:
        band: Band = "incompatible"
    elif not matched:
        band = "title_only"
    elif not measured or any(
        key in missing for key in ("episode_numbering", "episode_span", "physical_episode_or_movie")
    ):
        band = "possible_cut"
    elif FULL_ALGORITHM in equal:
        band = "exact_file"
    elif SAMPLED_ALGORITHM in equal:
        band = "strong_fingerprint"
    elif release_match:
        band = "release_match"
    else:
        band = "possible_cut"
    audio_conflicts = conflicts + [
        reason for reason in release_conflicts if reason.startswith("audio_")
    ]
    audio_missing = missing + ["analyzed_audio_stream"]
    if any(
        item.audio is None or not item.audio.language or item.audio.role == "unknown"
        for item in (left, right)
    ):
        audio_missing.append("audio_track_metadata")
    audio_band: Band = (
        "incompatible"
        if audio_conflicts
        else "title_only"
        if band == "title_only"
        else "possible_cut"
    )
    context_complete = (
        matched
        and measured
        and not any(
            key in missing
            for key in ("episode_numbering", "episode_span", "physical_episode_or_movie")
        )
    )
    channels = []
    for name, base_band, channel_conflicts, channel_missing in (
        ("video", band, conflicts, missing),
        ("audio", audio_band, audio_conflicts, audio_missing),
    ):
        first_stream = getattr(target, f"{name}_stream")
        second_stream = getattr(candidate, f"{name}_stream")
        if first_stream is None and second_stream is None:
            channels.append(_channel(base_band, reasons, channel_conflicts, channel_missing))
            continue
        stream_equal, stream_reasons, stream_conflicts, stream_missing = compare_streams(
            name, first_stream, second_stream
        )
        final_conflicts = channel_conflicts + stream_conflicts
        final_missing = [key for key in channel_missing if key != "analyzed_audio_stream"]
        if stream_equal:
            final_missing = [key for key in final_missing if key != "matching_fingerprint"]
        final_missing += stream_missing
        stream_ready = not stream_missing and "audio_track_metadata" not in final_missing
        if final_conflicts:
            final_band: Band = "incompatible"
        elif not matched:
            final_band = "title_only"
        elif not context_complete:
            final_band = "possible_cut"
        elif stream_equal and stream_ready:
            final_band = "exact_file" if FULL_ALGORITHM in equal else "strong_fingerprint"
        else:
            final_band = "possible_cut"
        channels.append(
            _channel(final_band, reasons + stream_reasons, final_conflicts, final_missing)
        )
    return Compatibility(POLICY_VERSION, channels[0], channels[1])


def classify_candidates(
    target: MatchEvidence, candidates: Iterable[tuple[str, MatchEvidence]], *, limit: int
) -> tuple[tuple[str, Compatibility], ...]:
    """Reject overflow rather than silently truncating; order by video/audio band and public ID."""
    if type(limit) is not int or limit < 1:
        raise ValueError("An explicit positive candidate limit is required")
    results: list[tuple[str, Compatibility]] = []
    seen: set[str] = set()
    for identifier, candidate in candidates:
        if len(results) >= limit:
            raise ValueError("Candidate limit exceeded")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", identifier) or identifier in seen:
            raise ValueError("Candidate IDs must be unique bounded public tokens")
        seen.add(identifier)
        results.append((identifier, classify(target, candidate)))
    return tuple(
        sorted(
            results,
            key=lambda item: (
                BANDS.index(item[1].video.band),
                BANDS.index(item[1].audio.band),
                item[0],
            ),
        )
    )


def compatibility_cache_key(
    target: MatchEvidence, candidate: MatchEvidence, *, policy_version: str = POLICY_VERSION
) -> tuple[str, MatchEvidence, MatchEvidence]:
    """Use policy version and all immutable evidence as a process-local comparison key."""
    return policy_version, target, candidate
