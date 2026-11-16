"""Strict local audio selection DTOs; physical indexes are not portable identity."""

from dataclasses import asdict

from pydantic import BaseModel, ConfigDict, Field

from scanner.pipeline.audio_tracks import AudioTrack, AudioTrackSelection


class AudioTrackMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stream_index: int = Field(ge=0, le=127, strict=True)
    language: str | None = Field(default=None, max_length=512)
    title: str | None = Field(default=None, max_length=512)
    codec: str | None = Field(default=None, max_length=128)
    channels: int | None = Field(default=None, ge=1, le=256, strict=True)
    disposition: int = Field(ge=0, strict=True)
    start_seconds: float | None = Field(default=None, allow_inf_nan=False)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    time_base: str | None = Field(default=None, max_length=64)


class AudioSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    track: AudioTrackMetadata

    def selection(self) -> AudioTrackSelection:
        return AudioTrackSelection(self.file_version, AudioTrack(**self.track.model_dump()))

    @classmethod
    def from_selection(cls, selection: AudioTrackSelection) -> "AudioSelection":
        return cls.model_validate(asdict(selection))


class AudioInventory(BaseModel):
    file_version: str
    tracks: list[AudioTrackMetadata] = Field(max_length=128)
    selection: AudioSelection | None = None
    selection_stale: bool = False


class AudioSelectionRequest(BaseModel):
    """A track for one file, named by a Jellyfin item and version."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=256)
    media_source_id: str | None = Field(default=None, max_length=256)
    selection: AudioSelection
