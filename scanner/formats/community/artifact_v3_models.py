"""Strict pydantic models for the `community-artifact-3` submission JSON (§5.2)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from .strict_json import MAX_SAFE_INTEGER

ARTIFACT_V3_SCHEMA = "community-artifact-3"
TOKEN = r"^[A-Za-z0-9_.-]{1,128}$"
KEY_ID = r"^sha256:[0-9a-f]{64}$"
PROVIDER_ID = r"^[0-9]{1,12}$"
ALIAS_ID = r"^[a-z0-9_]{1,64}$"
HEX64 = r"^[0-9a-f]{64}$"
MAX_DURATION_MS = 86_400_000

SafeInt = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
Token = Annotated[str, Field(pattern=TOKEN)]


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
    """Work identity; semantic rules (identifier, numbering, title) are checked by the codec."""

    kind: Literal["movie", "episode"]
    tmdb: str | None = Field(pattern=PROVIDER_ID)
    tvdb: str | None = Field(pattern=PROVIDER_ID)
    season: int | None = Field(ge=0, le=9999)
    episode: int | None = Field(ge=0, le=9999)
    last_episode: int | None = Field(ge=0, le=9999 + 15)
    title: str = Field(min_length=1)
    year: int | None = Field(ge=1870, le=2200)


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
    model_version: str | None = Field(min_length=1)


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
