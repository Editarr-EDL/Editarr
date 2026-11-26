"""Community match keys (§3.2) and builders for local media files."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .community_normalization import normalize

HashKeyType = Literal["sha256_full", "sha256_sampled", "md5_quick"]
StreamKeyType = Literal["audio_stream", "video_stream"]
AUDIO_STREAM_ALGORITHMS = frozenset(
    {
        "editarr-community-audio-pcm-s16le-16000-mono-sha256-1",
        "editarr-community-audio-sha256-sampled-1",
    }
)
VIDEO_STREAM_ALGORITHMS = frozenset({"editarr-community-video-sha256-sampled-1"})
KEY_TYPE_ORDER = (
    "sha256_full",
    "sha256_sampled",
    "md5_quick",
    "audio_stream",
    "video_stream",
    "tmdb",
    "tvdb",
    "title_year",
)
_HEX = {"md5_quick": re.compile(r"[0-9a-f]{32}")}
_HEX64 = re.compile(r"[0-9a-f]{64}")
_PROVIDER_ID = re.compile(r"[0-9]{1,12}")


@dataclass(frozen=True)
class HashKey:
    """A tier 1–2 file key; equal when type, value and `sizeBytes` are equal."""

    type: HashKeyType
    value: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not _HEX.get(self.type, _HEX64).fullmatch(self.value):
            raise ValueError("Invalid hash key value")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("Invalid hash key size")


@dataclass(frozen=True)
class StreamKey:
    """A tier 3 stream fingerprint; equal when type, algorithm and value are equal."""

    type: StreamKeyType
    algorithm: str
    value: str

    def __post_init__(self) -> None:
        audio = self.type == "audio_stream"
        allowed = AUDIO_STREAM_ALGORITHMS if audio else VIDEO_STREAM_ALGORITHMS
        if self.algorithm not in allowed or not _HEX64.fullmatch(self.value):
            raise ValueError("Invalid stream key")


@dataclass(frozen=True)
class ProviderKey:
    """A tier 4 `tmdb`/`tvdb` key; for episodes the ID is the series ID plus numbering."""

    type: Literal["tmdb", "tvdb"]
    kind: Literal["movie", "episode"]
    id: str
    season: int | None = None
    episode: int | None = None
    last_episode: int | None = None

    def __post_init__(self) -> None:
        if not _PROVIDER_ID.fullmatch(self.id):
            raise ValueError("Invalid provider ID")
        numbering = (self.season, self.episode, self.last_episode)
        if self.kind == "movie" and numbering != (None, None, None):
            raise ValueError("A movie key has no episode numbering")
        if self.kind == "episode" and (self.season is None or self.episode is None):
            raise ValueError("An episode key requires season and episode")


@dataclass(frozen=True)
class TitleYearKey:
    """A tier 5 key; equal when normalized titles and non-null years are equal."""

    title: str
    year: int | None

    @property
    def normalized(self) -> str:
        return normalize(self.title)

    def matches(self, other: "TitleYearKey") -> bool:
        return (
            self.year is not None
            and self.year == other.year
            and bool(self.normalized)
            and self.normalized == other.normalized
        )


FileKey = HashKey | StreamKey
WorkKey = ProviderKey | TitleYearKey
MatchKey = FileKey | WorkKey


def key_tier(key: MatchKey) -> int:
    """Return the ladder tier a key type can satisfy (§3.2)."""
    if isinstance(key, HashKey):
        return 1 if key.type == "sha256_full" else 2
    if isinstance(key, StreamKey):
        return 3
    return 4 if isinstance(key, ProviderKey) else 5


def keys_equal(left: MatchKey, right: MatchKey) -> bool:
    """Apply the per-type equality rule from §3.2."""
    if isinstance(left, TitleYearKey) and isinstance(right, TitleYearKey):
        return left.matches(right)
    return left == right


def md5_quick_key(path: str | Path) -> HashKey:
    """Build the `md5_quick` key from the existing sampled MD5 media fingerprint."""
    from scanner.runtime.media_fingerprints import sampled_media_fingerprint

    fingerprint = sampled_media_fingerprint(path)
    return HashKey("md5_quick", fingerprint.digest, fingerprint.size_bytes)
