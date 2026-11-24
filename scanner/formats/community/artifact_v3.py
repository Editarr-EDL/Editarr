"""Encode and strictly decode `community-artifact-3` submissions and their manifest (§5)."""

import hashlib
from dataclasses import dataclass

from pydantic import ValidationError

from scanner.modeling.community_match_keys import FileKey, HashKey, StreamKey
from scanner.modeling.community_normalization import normalize
from scanner.modeling.model_core import MCF_CATEGORY_TO_FAMILY

from .artifact_v3_models import ARTIFACT_V3_SCHEMA, ArtifactV3, FileHashKey, ManifestV3
from .strict_json import (
    StrictJsonError,
    canonical_json_bytes,
    gunzip_single_member,
    gzip_single_member,
    parse_strict_json,
)

MANIFEST_SCHEMA = "community-manifest-1"
CATEGORIES = frozenset(MCF_CATEGORY_TO_FAMILY.values())


@dataclass(frozen=True)
class ArtifactV3Limits:
    """Resource bounds checked before the artifact JSON is parsed (§5.2)."""

    compressed_bytes: int = 1_000_000
    expanded_bytes: int = 4_000_000
    depth: int = 8
    string_bytes: int = 512
    manifest_bytes: int = 4_096


DEFAULT_LIMITS = ArtifactV3Limits()


class ArtifactV3Error(ValueError):
    """An artifact or manifest is invalid; `code` is the service error it corresponds to."""

    def __init__(self, message: str, code: str = "invalid_submission") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DecodedArtifactV3:
    artifact: ArtifactV3
    gzip_bytes: bytes
    json_bytes: bytes

    @property
    def digest(self) -> str:
        """The submission digest: SHA-256 hex of the exact gzip bytes."""
        return hashlib.sha256(self.gzip_bytes).hexdigest()


def _bytes(value: str) -> int:
    return len(value.encode("utf-8"))


def _check_work(artifact: ArtifactV3) -> None:
    work = artifact.work
    if work.tmdb is None and work.tvdb is None:
        raise ArtifactV3Error("A TMDB or TVDB identifier is required", "work_identity_required")
    numbering = (work.season, work.episode, work.last_episode)
    if work.kind == "movie" and numbering != (None, None, None):
        raise ArtifactV3Error("A movie has no season or episode numbering")
    if work.kind == "episode":
        if work.season is None or work.episode is None:
            raise ArtifactV3Error("An episode requires season and episode")
        if work.last_episode is not None and not (
            work.episode <= work.last_episode <= work.episode + 15
        ):
            raise ArtifactV3Error("lastEpisode must be within episode..episode + 15")
    if _bytes(work.title) > 512 or not normalize(work.title):
        raise ArtifactV3Error("The title must be 1–512 bytes and normalize to non-empty text")


def _check_media(artifact: ArtifactV3) -> None:
    keys = artifact.media.match_keys
    if len({key.type for key in keys}) != len(keys):
        raise ArtifactV3Error("Each match key type may appear at most once")
    sizes = {key.size_bytes for key in keys if isinstance(key, FileHashKey)}
    if len(sizes) > 1:
        raise ArtifactV3Error("All match key sizeBytes values must be equal")
    for key in keys:
        expected = 32 if key.type == "md5_quick" else 64
        if isinstance(key, FileHashKey) and len(key.value) != expected:
            raise ArtifactV3Error("Invalid match key digest length")


def _check_bounds(start: int, end: int, duration: int) -> None:
    if not 0 <= start < end <= duration:
        raise ArtifactV3Error(
            "Times must satisfy 0 <= startMs < endMs <= durationMs", "invalid_bounds"
        )


def _check_coverage(artifact: ArtifactV3) -> None:
    categories = [coverage.category for coverage in artifact.coverage]
    if len(set(categories)) != len(categories):
        raise ArtifactV3Error("Coverage categories must be unique")
    for coverage in artifact.coverage:
        if coverage.category not in CATEGORIES:
            raise ArtifactV3Error("Unknown coverage category", "invalid_taxonomy")
        previous_end = 0
        for interval in coverage.intervals:
            _check_bounds(interval.start_ms, interval.end_ms, artifact.media.duration_ms)
            if interval.start_ms < previous_end:
                raise ArtifactV3Error("Coverage intervals must be sorted and non-overlapping",
                                      "invalid_bounds")
            previous_end = interval.end_ms


def _check_entries(artifact: ArtifactV3) -> None:
    entry_ids: set[str] = set()
    filter_ids: set[str] = set()
    for entry in artifact.entries:
        if entry.entry_id in entry_ids:
            raise ArtifactV3Error("Duplicate entryId")
        entry_ids.add(entry.entry_id)
        if entry.proposed_filter_id is not None:
            if entry.proposed_filter_id in filter_ids:
                raise ArtifactV3Error("A proposedFilterId may appear at most once")
            filter_ids.add(entry.proposed_filter_id)
        elif entry.kind != "support":
            raise ArtifactV3Error("Disputes and coverage negatives require a proposedFilterId")
        classification = (entry.subcategory, entry.severity, entry.action)
        if entry.kind == "support" and None in classification:
            raise ArtifactV3Error("Support requires subcategory, severity and action")
        if entry.kind != "support" and classification != (None, None, None):
            raise ArtifactV3Error("Only support entries carry a classification")
        evidence = entry.evidence
        if evidence.model_version is not None and _bytes(evidence.model_version) > 128:
            raise ArtifactV3Error("modelVersion exceeds 128 bytes")
        if evidence.origin == "model" and evidence.model_version is None:
            raise ArtifactV3Error("Model evidence requires modelVersion")
        if entry.category not in CATEGORIES:
            raise ArtifactV3Error("Unknown entry category", "invalid_taxonomy")
        if entry.subcategory is not None and (
            MCF_CATEGORY_TO_FAMILY.get(entry.subcategory) != entry.category
        ):
            raise ArtifactV3Error("Subcategory is not in the category family", "invalid_taxonomy")
        _check_bounds(entry.start_ms, entry.end_ms, artifact.media.duration_ms)


def validate_artifact_v3(artifact: ArtifactV3) -> ArtifactV3:
    """Apply the §5.2 rules that a schema alone cannot express."""
    _check_work(artifact)
    _check_media(artifact)
    _check_coverage(artifact)
    _check_entries(artifact)
    return artifact


def artifact_json_bytes(artifact: ArtifactV3) -> bytes:
    """Return the canonical compact UTF-8 JSON for a valid artifact (§5.2 key order)."""
    validate_artifact_v3(artifact)
    return canonical_json_bytes(artifact.model_dump(mode="json", by_alias=True))


def encode_artifact_v3(
    artifact: ArtifactV3, *, limits: ArtifactV3Limits = DEFAULT_LIMITS
) -> DecodedArtifactV3:
    """Encode an artifact to deterministic gzip bytes and prove they decode within limits."""
    try:
        compressed = gzip_single_member(artifact_json_bytes(artifact))
    except StrictJsonError as exc:
        raise ArtifactV3Error(str(exc)) from exc
    return decode_artifact_v3(compressed, limits=limits)


def decode_artifact_v3(
    gzip_bytes: bytes, *, limits: ArtifactV3Limits = DEFAULT_LIMITS
) -> DecodedArtifactV3:
    """Strictly decode exact gzip bytes, checking resource bounds before parsing JSON."""
    try:
        expanded = gunzip_single_member(
            gzip_bytes, max_compressed=limits.compressed_bytes, max_expanded=limits.expanded_bytes
        )
        value = parse_strict_json(
            expanded,
            max_bytes=limits.expanded_bytes,
            max_depth=limits.depth,
            max_string_bytes=limits.string_bytes,
        )
        artifact = ArtifactV3.model_validate(value, strict=True, by_alias=True, by_name=False)
    except (StrictJsonError, ValidationError) as exc:
        raise ArtifactV3Error(f"Invalid {ARTIFACT_V3_SCHEMA}: {exc}") from exc
    return DecodedArtifactV3(validate_artifact_v3(artifact), gzip_bytes, expanded)


def build_manifest_v3(decoded: DecodedArtifactV3) -> bytes:
    """Build manifest bytes for exact artifact gzip bytes; `summary` is always `{}`."""
    artifact = decoded.artifact
    return canonical_json_bytes(
        {
            "schema_version": MANIFEST_SCHEMA,
            "artifact_format": "gzip-json",
            "artifact_schema_version": ARTIFACT_V3_SCHEMA,
            "artifact_digest": decoded.digest,
            "submission_id": artifact.submission_id,
            "contributor_id": artifact.author.contributor_id,
            "key_id": artifact.author.key_id,
            "summary": {},
        }
    )


def parse_manifest_v3(
    raw: bytes, *, decoded: DecodedArtifactV3, limits: ArtifactV3Limits = DEFAULT_LIMITS
) -> ManifestV3:
    """Strictly parse manifest bytes and bind them to the decoded artifact's exact bytes."""
    try:
        value = parse_strict_json(
            raw, max_bytes=limits.manifest_bytes, max_string_bytes=limits.string_bytes
        )
        manifest = ManifestV3.model_validate(value, strict=True)
    except (StrictJsonError, ValidationError) as exc:
        raise ArtifactV3Error(f"Invalid {MANIFEST_SCHEMA}: {exc}") from exc
    artifact = decoded.artifact
    if manifest.summary != {}:
        raise ArtifactV3Error("Manifest summary must be {}")
    if manifest.artifact_digest != decoded.digest:
        raise ArtifactV3Error("Manifest digest does not match the artifact gzip bytes")
    if (manifest.submission_id, manifest.contributor_id, manifest.key_id) != (
        artifact.submission_id,
        artifact.author.contributor_id,
        artifact.author.key_id,
    ):
        raise ArtifactV3Error("Manifest identity does not match the artifact")
    return manifest


def artifact_file_keys(artifact: ArtifactV3) -> tuple[FileKey, ...]:
    """Convert artifact tier 1–3 match keys into ladder keys."""
    return tuple(
        HashKey(key.type, key.value, key.size_bytes)
        if isinstance(key, FileHashKey)
        else StreamKey(key.type, key.algorithm, key.value)
        for key in artifact.media.match_keys
    )
