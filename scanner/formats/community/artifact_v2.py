"""V2 source snapshots and already-applied target-timeline lineage."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from scanner.modeling.community_matching import MatchEvidence

PUBLIC_EXECUTION_SETTINGS = frozenset(
    {
        "scene_detection_threshold",
        "coarse_scan_enabled",
        "coarse_scan_interval_seconds",
        "qwen_summaries_per_segment",
        "qwen_segment_join_gap_seconds",
        "qwen_model_requested",
        "audio_filter",
        "whisper_model",
        "detector_version",
    }
)

_ARR_DURATION_SOURCES = frozenset({"radarr_catalog_minutes", "sonarr_catalog_minutes"})
_ARR_PROVENANCE = re.compile(r"^(radarr|sonarr)\.", re.IGNORECASE | re.ASCII)


def _without_arr_identifiers(items):
    if not isinstance(items, list):
        return items
    return [
        {**item, "provenance": "source_metadata"}
        if isinstance(item, dict)
        and isinstance(item.get("provenance"), str)
        and _ARR_PROVENANCE.match(item["provenance"])
        else item
        for item in items
    ]


def without_arr_provenance(matching):
    """Read evidence signed before community migration 0017 the way 0017 rewrote catalog copies."""
    media = matching.get("media") if isinstance(matching, dict) else None
    if not isinstance(media, dict):
        return matching
    media = {
        **media,
        **{
            key: _without_arr_identifiers(media[key])
            for key in ("identifiers", "series_identifiers")
            if key in media
        },
    }
    release = media.get("release")
    duration = release.get("duration") if isinstance(release, dict) else None
    if isinstance(duration, dict) and duration.get("source") in _ARR_DURATION_SOURCES:
        media["release"] = {**release, "duration": {**duration, "source": "jellyfin_ticks"}}
    return {**matching, "media": media}


class SourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(pattern=r"^source-[1-9][0-9]*$")
    origin: Literal["generated", "manual", "imported"]
    status: Literal["complete", "failed", "cancelled"]
    created_at: str
    revision: int = Field(ge=0, le=(1 << 53) - 1)
    duration_seconds: float | None = Field(gt=0, allow_inf_nan=False)
    provenance: dict
    coverage: list[dict]
    reference: dict | None
    author_id: str | None


class AnalyzedAudio(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provenance: Literal["analyzed"]
    language: str | None
    description: str | None
    role: Literal["main", "commentary", "descriptive", "unknown"]
    codec: str | None
    channels: int | None = Field(ge=1, le=256)
    stream_start_seconds: float | None = Field(allow_inf_nan=False)
    stream_duration_seconds: float | None = Field(ge=0, allow_inf_nan=False)
    time_base: str | None


class TimingBounds(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)


class Retiming(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str
    source_bounds: TimingBounds | None
    target_bounds: TimingBounds
    mapping: Literal["affine", "unknown"]
    applied: Literal[True]


def validate_v2(artifact, *, source_limit: int) -> MatchEvidence:
    """Validate portable matching inputs and lineage without looking up local or remote data."""
    from .codec import _SETTINGS, ArtifactError, Coverage, Parent, Provenance, _interval

    if not artifact.matching or artifact.sources is None or artifact.retiming is None:
        raise ArtifactError("V2 requires matching evidence, source snapshots and retiming records")
    try:
        matching = TypeAdapter(MatchEvidence).validate_json(
            json.dumps(without_arr_provenance(artifact.matching)), strict=True, extra="forbid"
        )
    except (ValueError, TypeError) as exc:
        raise ArtifactError("Invalid V2 matching evidence") from exc
    identity = matching.media
    work_ids = [
        {"provider": item.provider, "entity_type": item.entity_type, "value": item.value}
        for item in (*identity.identifiers, *identity.series_identifiers)
    ]
    if work_ids != [item.model_dump() for item in artifact.media.work_ids]:
        raise ArtifactError("V2 work identity disagrees with media")
    if (
        identity.release.duration is not None
        and identity.release.duration.seconds != artifact.media.duration_seconds
    ):
        raise ArtifactError("V2 matching duration disagrees with timeline")
    if len(artifact.sources) > source_limit or len(artifact.retiming) > source_limit:
        raise ArtifactError("V2 source lineage exceeds configured bound")
    sources = {item.source_id: item for item in artifact.sources}
    if len(sources) != len(artifact.sources):
        raise ArtifactError("Duplicate V2 source snapshot")
    for source in artifact.sources:
        provenance = Provenance.model_validate(source.provenance, strict=True)
        coverage = [Coverage.model_validate(item, strict=True) for item in source.coverage]
        if source.origin != "generated" and provenance.stages:
            raise ArtifactError("Non-generated source cannot claim scanner execution")
        if source.reference is not None:
            Parent.model_validate(source.reference, strict=True)
        for record in [*provenance.stages, *coverage]:
            for interval in record.intervals:
                if source.duration_seconds is None:
                    raise ArtifactError("Source coverage has unknown timeline bounds")
                _interval(interval.start_seconds, interval.end_seconds, source.duration_seconds)
        if any(
            stage.settings.keys() - (_SETTINGS | PUBLIC_EXECUTION_SETTINGS)
            for stage in provenance.stages
        ):
            raise ArtifactError("Unsupported source effective setting")
    if artifact.audio_association == "identified":
        source = sources.get(artifact.audio_source_id)
        if source is None or source.origin != "generated" or artifact.analyzed_audio is None:
            raise ArtifactError("Identified audio requires its generated source snapshot")
        if matching.audio_stream is not None and matching.audio_stream.provenance != "analyzed":
            raise ArtifactError("Identified audio must be actually analyzed")
        metadata = artifact.analyzed_audio
        track = identity.release.audio
        if track is None or (track.language, track.role, track.codec, track.channels) != (
            metadata.language,
            metadata.role,
            metadata.codec,
            metadata.channels,
        ):
            raise ArtifactError("Analyzed audio metadata disagrees with matching evidence")
        if (
            matching.audio_stream is not None
            and matching.audio_stream.description != metadata.description
        ):
            raise ArtifactError("Analyzed audio description disagrees with matching evidence")
    elif (
        matching.audio_stream is not None
        or identity.release.audio is not None
        or artifact.audio_source_id is not None
        or artifact.analyzed_audio is not None
    ):
        raise ArtifactError("Unknown audio cannot claim an identified stream or track")
    if artifact.audio_association == "none" and any(
        item.channel in {"audio", "both"} for item in artifact.findings
    ):
        raise ArtifactError("Audio findings require explicit association")
    for finding in artifact.findings:
        if finding.source_snapshot_id is not None and finding.source_snapshot_id not in sources:
            raise ArtifactError("Finding refers to unknown source snapshot")
    seen = set()
    if artifact.kind != "derived" and artifact.retiming:
        raise ArtifactError("Only derived target timelines can contain retiming")
    for record in artifact.retiming:
        if record.source_id not in sources or record.source_id in seen:
            raise ArtifactError("Retiming requires one record per known source")
        seen.add(record.source_id)
        for timing in (record.source_bounds, record.target_bounds):
            if timing is not None and timing.end_seconds <= timing.start_seconds:
                raise ArtifactError("Invalid retiming bounds")
        if (record.mapping == "affine") != (record.source_bounds is not None):
            raise ArtifactError("Unknown retiming cannot fabricate source bounds")
        if record.target_bounds.end_seconds > artifact.media.duration_seconds:
            raise ArtifactError("Retiming target exceeds artifact timeline")
        source_duration = sources[record.source_id].duration_seconds
        if (
            record.source_bounds is not None
            and source_duration is not None
            and record.source_bounds.end_seconds > source_duration
        ):
            raise ArtifactError("Retiming source exceeds source timeline")
    if artifact.kind == "derived" and seen != sources.keys():
        raise ArtifactError("Derived artifact requires explicit retiming state for every source")
    declared = {(item.submission_id, item.digest) for item in artifact.source_references}
    if artifact.derivation:
        declared.update((item.submission_id, item.digest) for item in artifact.derivation.parents)
    if any(
        source.reference is not None
        and (source.reference["submission_id"], source.reference["digest"]) not in declared
        for source in artifact.sources
    ):
        raise ArtifactError("Source reference is missing from declared lineage")
    return matching
