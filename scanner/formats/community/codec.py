"""Strict, bounded community artifact decoding without local database access."""

import gzip
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scanner.models import MCF_CATEGORY_TO_FAMILY

from .artifact_v2 import (
    PUBLIC_EXECUTION_SETTINGS,
    AnalyzedAudio,
    Retiming,
    SourceSnapshot,
    validate_v2,
    without_arr_provenance,
)
from .contribution_metadata import ManualFilterEvidence, contribution_metadata


class ArtifactError(ValueError):
    """An artifact cannot be safely decoded under the supplied bounds."""


@dataclass(frozen=True)
class ResourceBounds:
    compressed_bytes: int
    expanded_bytes: int
    events: int
    text: int
    depth: int

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("Resource bounds must be positive integers")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_min_length=1)


class Interval(StrictModel):
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(ge=0, allow_inf_nan=False)


class Author(StrictModel):
    contributor_id: str
    key_id: str


class WorkId(StrictModel):
    provider: str
    entity_type: str
    value: str


class Media(StrictModel):
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    timeline: Literal["source_file", "derived_target"]
    time_unit: Literal["seconds"]
    work_ids: list[WorkId]
    release: str | None = None
    fingerprint_algorithm: str | None = None
    fingerprint_digest: str | None = None
    audio_track: str | None = None
    audio_language: str | None = None


class Stage(StrictModel):
    execution_id: str
    capability: str
    state: Literal["complete", "partial", "failed", "disabled", "unsupported", "unknown"]
    model_id: str | None
    model_revision: str | None
    model_digest: str | None
    settings: dict[str, str | float | bool | None]
    intervals: list[Interval]
    sampling_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    release_version: str | None = None
    scanner_behavior_version: str | None = None
    build_revision: str | None = None
    quantization: str | None = None


class Provenance(StrictModel):
    release_version: str | None
    scanner_behavior_version: str | None
    stages: list[Stage]
    build_revision: str | None = None


class Coverage(StrictModel):
    channel: Literal["audio", "video", "both"]
    capability: str
    state: Literal["complete", "partial", "failed", "disabled", "unsupported", "unknown"]
    intervals: list[Interval]


class Finding(StrictModel):
    event_id: str
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(ge=0, allow_inf_nan=False)
    source: Literal["audio", "visual", "manual", "derived"]
    channel: Literal["audio", "video", "both"]
    category: str | None
    subcategory: str | None
    severity: Literal["low", "medium", "high"] | None
    confidence: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    action: Literal["skip", "mute"] | None
    note: str | None
    parent_event_id: str | None
    source_snapshot_id: str | None = None
    manual: ManualFilterEvidence | None = None


class Parent(StrictModel):
    submission_id: str
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class Derivation(StrictModel):
    parents: list[Parent]
    transform: str | None


class Artifact(StrictModel):
    schema_version: Literal["community-artifact-1", "community-artifact-2"]
    name: str | None = None
    source_revision: int | None = Field(default=None, ge=0, le=(1 << 53) - 1)
    matching: dict | None = None
    sources: list[SourceSnapshot] | None = None
    retiming: list[Retiming] | None = None
    audio_association: Literal["none", "unknown", "identified"] | None = None
    audio_source_id: str | None = None
    analyzed_audio: AnalyzedAudio | None = None
    manual_filter_tracking: Literal["manual-1"] | None = None
    submission_id: str
    kind: Literal["scan", "manual", "derived"]
    created_at: str
    author: Author
    sharing_terms: str | None
    source_references: list[Parent] = Field(default_factory=list)
    media: Media
    provenance: Provenance
    coverage: list[Coverage]
    findings: list[Finding]
    derivation: Derivation | None
    supersedes: str | None = None


@dataclass(frozen=True)
class DecodedArtifact:
    artifact: Artifact
    stored_bytes: bytes
    summary: dict
    compressed: bool


class Manifest(StrictModel):
    schema_version: Literal["community-manifest-1"]
    artifact_format: Literal["gzip-json"]
    artifact_schema_version: Literal["community-artifact-1", "community-artifact-2"]
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_id: str
    contributor_id: str
    key_id: str
    summary: dict


@dataclass(frozen=True)
class DecodedManifest:
    manifest: Manifest
    stored_bytes: bytes


_SETTINGS = frozenset(
    {
        "frame_interval_seconds",
        "speed_up",
        "audio_model",
        "video_model",
        "qwen_model",
        "credits_model",
    }
)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _check_tree(value: object, bounds: ResourceBounds, depth: int = 0) -> None:
    if depth > bounds.depth:
        raise ArtifactError("Artifact nesting exceeds configured depth")
    if isinstance(value, str):
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError as exc:
            raise ArtifactError("Invalid Unicode in artifact") from exc
        if size > bounds.text:
            raise ArtifactError("Artifact text exceeds configured length")
    if isinstance(value, float) and not math.isfinite(value):
        raise ArtifactError("Nonfinite number in artifact")
    if isinstance(value, dict):
        for key, item in value.items():
            _check_tree(key, bounds, depth + 1)
            _check_tree(item, bounds, depth + 1)
    if isinstance(value, list):
        for item in value:
            _check_tree(item, bounds, depth + 1)


def _interval(start: float, end: float, duration: float) -> None:
    if start > end or end > duration:
        raise ArtifactError("Interval outside declared source timeline")


def _equal_json(left: object, right: object) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _equal_json(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _equal_json(item, other) for item, other in zip(left, right)
        )
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        return isinstance(right, (int, float)) and not isinstance(right, bool) and left == right
    return type(left) is type(right) and left == right


def _summary(artifact: Artifact) -> dict:
    sources: dict[str, int] = {}
    channels: dict[str, int] = {}
    taxonomy: dict[str, int] = {}
    for finding in artifact.findings:
        sources[finding.source] = sources.get(finding.source, 0) + 1
        for channel in ["audio", "video"] if finding.channel == "both" else [finding.channel]:
            channels[channel] = channels.get(channel, 0) + 1
        label = (
            f"{finding.category}/{finding.subcategory}"
            if finding.category is not None
            else "unclassified"
        )
        taxonomy[label] = taxonomy.get(label, 0) + 1
    result = {
        "schema_version": "community-summary-2"
        if artifact.schema_version == "community-artifact-2"
        else "community-summary-1",
        "registry_version": "mcf-1.1.0",
        "submission_id": artifact.submission_id,
        "kind": artifact.kind,
        "work_ids": [work_id.model_dump(mode="json") for work_id in artifact.media.work_ids],
        "release": artifact.media.release,
        "duration_seconds": artifact.media.duration_seconds,
        "release_version": artifact.provenance.release_version,
        "scanner_behavior_version": artifact.provenance.scanner_behavior_version,
        "total": len(artifact.findings),
        "sources": sources,
        "channels": channels,
        "taxonomy": taxonomy,
        "coverage": [coverage.model_dump(mode="json") for coverage in artifact.coverage],
    }
    if artifact.schema_version == "community-artifact-2":
        result["name"] = artifact.name
        result["matching"] = artifact.matching
        result["audio_association"] = artifact.audio_association
        result["source_revision"] = artifact.source_revision
        result["source_states"] = [
            {"source_id": item.source_id, "origin": item.origin, "status": item.status}
            for item in artifact.sources
        ]
        result["analyzed_audio"] = (
            artifact.analyzed_audio.model_dump(mode="json") if artifact.analyzed_audio else None
        )
    return {**result, **contribution_metadata(artifact)}


def parse_artifact(
    raw: bytes, *, bounds: ResourceBounds, compressed: bool = False
) -> DecodedArtifact:
    """Validate exact transport bytes under caller-approved bounds; never normalize signed input."""
    if len(raw) > (bounds.compressed_bytes if compressed else bounds.expanded_bytes):
        raise ArtifactError("Artifact exceeds configured byte limit")
    if compressed:
        try:
            import io

            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                expanded = stream.read(bounds.expanded_bytes + 1)
        except (OSError, EOFError) as exc:
            raise ArtifactError("Invalid gzip artifact") from exc
    else:
        expanded = raw
    if len(expanded) > bounds.expanded_bytes:
        raise ArtifactError("Expanded artifact exceeds configured byte limit")
    try:
        value = json.loads(
            expanded.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ArtifactError(f"Nonfinite number: {token}")
            ),
        )
        _check_tree(value, bounds)
        artifact = Artifact.model_validate(value, strict=True)
    except (UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ArtifactError(f"Invalid community artifact: {exc}") from exc
    if len(artifact.findings) > bounds.events:
        raise ArtifactError("Artifact exceeds configured event count")
    v2_fields = {
        "name",
        "source_revision",
        "matching",
        "sources",
        "retiming",
        "audio_association",
        "audio_source_id",
        "analyzed_audio",
        "manual_filter_tracking",
    }
    if artifact.schema_version == "community-artifact-1" and (
        v2_fields & value.keys()
        or any({"source_snapshot_id", "manual"} & item.keys() for item in value["findings"])
        or any(
            {"release_version", "scanner_behavior_version", "build_revision", "quantization"}
            & item.keys()
            for item in value["provenance"]["stages"]
        )
    ):
        raise ArtifactError("V1 artifact cannot contain V2 fields")
    if artifact.manual_filter_tracking is not None:
        if any(finding.manual is None for finding in artifact.findings):
            raise ArtifactError("Manual filter tracking requires evidence for every finding")
    elif "manual_filter_tracking" in value or any("manual" in item for item in value["findings"]):
        raise ArtifactError("Manual filter evidence requires a tracking version")
    if artifact.schema_version == "community-artifact-2":
        if (
            not artifact.name
            or artifact.source_revision is None
            or artifact.audio_association is None
        ):
            raise ArtifactError("V2 artifact requires name, revision and audio association")
        try:
            matching = validate_v2(artifact, source_limit=bounds.events)
        except ArtifactError:
            raise
        except (ValueError, TypeError) as exc:
            raise ArtifactError("Invalid V2 source evidence") from exc
        from pydantic import TypeAdapter

        from scanner.modeling.community_matching import MatchEvidence

        artifact.matching = TypeAdapter(MatchEvidence).dump_python(matching, mode="json")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", artifact.created_at):
        raise ArtifactError("Creation time must be UTC seconds")
    if artifact.kind == "derived" and (
        artifact.derivation is None
        or not (
            artifact.derivation.parents
            or (artifact.schema_version == "community-artifact-2" and artifact.sources)
        )
        or artifact.media.timeline != "derived_target"
    ):
        raise ArtifactError("Derived artifact requires parent lineage and target timeline")
    if artifact.kind != "derived" and artifact.derivation is not None:
        raise ArtifactError("Non-derived artifact cannot claim derived lineage")
    if artifact.kind == "manual" and artifact.provenance.stages:
        raise ArtifactError("Manual artifact cannot claim scanner execution")
    event_ids = set()
    for finding in artifact.findings:
        _interval(finding.start_seconds, finding.end_seconds, artifact.media.duration_seconds)
        if not finding.event_id or finding.event_id in event_ids:
            raise ArtifactError("Missing or duplicate event ID")
        event_ids.add(finding.event_id)
        if (finding.category is None) != (finding.subcategory is None):
            raise ArtifactError("Incomplete classification pair")
        if (
            finding.subcategory is not None
            and MCF_CATEGORY_TO_FAMILY.get(finding.subcategory) != finding.category
        ):
            raise ArtifactError("Unknown classification pair")
        if finding.source == "derived" and artifact.kind != "derived":
            raise ArtifactError("Derived finding without derived artifact")
    for finding in artifact.findings:
        if finding.parent_event_id is not None and artifact.kind != "derived":
            raise ArtifactError("Parent event outside derived artifact")
    for record in [*artifact.coverage, *artifact.provenance.stages]:
        for interval in record.intervals:
            _interval(interval.start_seconds, interval.end_seconds, artifact.media.duration_seconds)
    for stage in artifact.provenance.stages:
        allowed_settings = (
            _SETTINGS | PUBLIC_EXECUTION_SETTINGS
            if artifact.schema_version == "community-artifact-2"
            else _SETTINGS
        )
        if stage.settings.keys() - allowed_settings:
            raise ArtifactError("Unsupported effective setting")
    return DecodedArtifact(artifact, raw, _summary(artifact), compressed)


def _signed_summary(summary: dict) -> dict:
    if "matching" not in summary:
        return summary
    return {**summary, "matching": without_arr_provenance(summary["matching"])}


def parse_manifest(
    raw: bytes, *, artifact: DecodedArtifact, bounds: ResourceBounds
) -> DecodedManifest:
    """Bind exact stored gzip bytes to immutable author claims and computed summary."""
    if not artifact.compressed:
        raise ArtifactError("Manifest requires gzip JSON artifact bytes")
    if len(raw) > bounds.expanded_bytes:
        raise ArtifactError("Manifest exceeds configured byte limit")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_unique_pairs)
        _check_tree(value, bounds)
        manifest = Manifest.model_validate(value, strict=True)
    except (UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ArtifactError(f"Invalid community manifest: {exc}") from exc
    if (
        manifest.artifact_schema_version != artifact.artifact.schema_version
        or manifest.artifact_digest != hashlib.sha256(artifact.stored_bytes).hexdigest()
        or manifest.submission_id != artifact.artifact.submission_id
        or manifest.contributor_id != artifact.artifact.author.contributor_id
        or manifest.key_id != artifact.artifact.author.key_id
        or not _equal_json(_signed_summary(manifest.summary), artifact.summary)
    ):
        raise ArtifactError("Manifest does not match artifact bytes, identity, or summary")
    return DecodedManifest(manifest, raw)
