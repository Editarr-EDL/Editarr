"""Strict pydantic models for the `community-artifact-3` submission JSON (§5.2)."""

import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from scanner.modeling.community_normalization import normalize

from .strict_json import MAX_SAFE_INTEGER

ARTIFACT_V3_SCHEMA = "community-artifact-3"
TOKEN = r"^[A-Za-z0-9_.-]{1,128}$"
KEY_ID = r"^sha256:[0-9a-f]{64}$"
PROVIDER_ID = r"^[0-9]{1,12}$"
ALIAS_ID = r"^[a-z0-9_]{1,64}$"
HEX64 = r"^[0-9a-f]{64}$"
MAX_DURATION_MS = 86_400_000
EXPRESSION_MAX_CODE_POINTS = 64

SafeInt = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
Token = Annotated[str, Field(pattern=TOKEN)]


def normalize_expression(value: str | None) -> str | None:
    """A language filter's word or phrase as shared (§5.2), or None when it can't be.

    Control (Cc), format (Cf) and lone surrogate (Cs) characters make it unusable;
    otherwise it is NFC normalized and trimmed, and must then hold 1–64 code points.
    """
    if not isinstance(value, str) or any(
        unicodedata.category(character) in ("Cc", "Cf", "Cs") for character in value
    ):
        return None
    normalized = unicodedata.normalize("NFC", value).strip()
    return normalized if 1 <= len(normalized) <= EXPRESSION_MAX_CODE_POINTS else None


class StrictCamelModel(BaseModel):
    """Frozen, strict model whose JSON names are camelCase; unknown fields are rejected."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


class Author(StrictCamelModel):
    contributor_id: Token
    key_id: str = Field(pattern=KEY_ID)


class Work(StrictCamelModel):
    """Work identity; `check_work` applies the rules a schema cannot express."""

    kind: Literal["movie", "episode"]
    tmdb: str | None = Field(pattern=PROVIDER_ID)
    tvdb: str | None = Field(pattern=PROVIDER_ID)
    season: int | None = Field(ge=0, le=9999)
    episode: int | None = Field(ge=0, le=9999)
    last_episode: int | None = Field(ge=0, le=9999 + 15)
    title: str = Field(min_length=1)
    year: int | None = Field(ge=1870, le=2200)


class WorkError(ValueError):
    """A work violates §5.2 identity, numbering or title rules; `code` is the service error."""

    def __init__(self, message: str, code: str = "invalid_submission") -> None:
        super().__init__(message)
        self.code = code


def check_work(work: Work) -> None:
    """Require a provider ID, kind-consistent numbering and a 1–256 code point normalized title."""
    if work.tmdb is None and work.tvdb is None:
        raise WorkError("A TMDB or TVDB identifier is required", "work_identity_required")
    numbering = (work.season, work.episode, work.last_episode)
    if work.kind == "movie" and numbering != (None, None, None):
        raise WorkError("A movie has no season or episode numbering")
    if work.kind == "episode":
        if work.season is None or work.episode is None:
            raise WorkError("An episode requires season and episode")
        if work.last_episode is not None and not (
            work.episode <= work.last_episode <= work.episode + 15
        ):
            raise WorkError("lastEpisode must be within episode..episode + 15")
    if len(work.title.encode("utf-8")) > 512:
        raise WorkError("The title must be 1–512 bytes")
    if not 1 <= len(normalize(work.title)) <= 256:
        raise WorkError("The normalized title must contain 1–256 code points")


class AliasVersion(StrictCamelModel):
    choice: Literal["alias"]
    alias_id: str = Field(pattern=ALIAS_ID)


class OtherVersion(StrictCamelModel):
    choice: Literal["other"]
    text: str | None = Field(max_length=64)


VersionChoice = Annotated[AliasVersion | OtherVersion, Field(discriminator="choice")]


class FileHashKey(StrictCamelModel):
    type: Literal["sha256_full", "sha256_sampled", "md5_quick"]
    value: str = Field(pattern=r"^(?:[0-9a-f]{32}|[0-9a-f]{64})$")
    size_bytes: SafeInt


class AudioStreamKey(StrictCamelModel):
    type: Literal["audio_stream"]
    algorithm: Literal[
        "editarr-community-audio-pcm-s16le-16000-mono-sha256-1",
        "editarr-community-audio-sha256-sampled-1",
    ]
    value: str = Field(pattern=HEX64)


class VideoStreamKey(StrictCamelModel):
    type: Literal["video_stream"]
    algorithm: Literal["editarr-community-video-sha256-sampled-1"]
    value: str = Field(pattern=HEX64)


ArtifactMatchKey = Annotated[
    FileHashKey | AudioStreamKey | VideoStreamKey, Field(discriminator="type")
]


class Media(StrictCamelModel):
    duration_ms: int = Field(ge=1, le=MAX_DURATION_MS)
    match_keys: list[ArtifactMatchKey] = Field(max_length=5)


class AlignmentHint(StrictCamelModel):
    version_id: Token
    revision: int = Field(ge=1, le=MAX_SAFE_INTEGER)
    offset_ms: int = Field(ge=-MAX_DURATION_MS, le=MAX_DURATION_MS)
    scale: float = Field(ge=0.9, le=1.1, allow_inf_nan=False)
    anchors: int = Field(ge=0, le=2000)


class Interval(StrictCamelModel):
    start_ms: SafeInt
    end_ms: SafeInt


class Coverage(StrictCamelModel):
    category: str
    intervals: list[Interval] = Field(min_length=1, max_length=512)


class Evidence(StrictCamelModel):
    origin: Literal["model", "manual", "downloaded"]
    manual_review: bool
    locally_verified: bool
    model_version: str | None


class Entry(StrictCamelModel):
    entry_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    kind: Literal["support", "dispute", "coverage_negative"]
    proposed_filter_id: Token | None
    start_ms: SafeInt
    end_ms: SafeInt
    category: str
    subcategory: str | None
    severity: Literal["low", "medium", "high"] | None
    action: Literal["skip", "mute"] | None
    expression: str | None = None
    evidence: Evidence


class ArtifactV3(StrictCamelModel):
    """A `community-artifact-3` submission; all times are file-timeline milliseconds."""

    schema_version: Literal["community-artifact-3"]
    submission_id: Token
    created_at: SafeInt
    author: Author
    work: Work
    version: VersionChoice
    media: Media
    alignment_hint: AlignmentHint | None
    coverage: list[Coverage] = Field(max_length=9)
    entries: list[Entry] = Field(max_length=2000)


class ManifestV3(BaseModel):
    """A `community-manifest-1` manifest bound to a `community-artifact-3` gzip."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["community-manifest-1"]
    artifact_format: Literal["gzip-json"]
    artifact_schema_version: Literal["community-artifact-3"]
    artifact_digest: str = Field(pattern=HEX64)
    submission_id: Token
    contributor_id: Token
    key_id: str = Field(pattern=KEY_ID)
    summary: dict
