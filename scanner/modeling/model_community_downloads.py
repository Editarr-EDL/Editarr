"""Strict one-item downloads and bounded local reconciliation inputs."""

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from .model_community_lookup import LookupModel, LookupRequest, LookupTarget


class DownloadChoice(LookupModel):
    contributionId: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    revisionId: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")
    artifactDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    channels: list[Literal["audio", "video"]] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def unique_channels(self):
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("Duplicate channels")
        return self


class DownloadRequest(LookupModel):
    lookup: LookupRequest
    choice: DownloadChoice
    confirmed: StrictBool = False
    stage_for_verification: StrictBool = False


class DownloadStatusRequest(LookupModel):
    target: LookupTarget
    target_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    choices: list[DownloadChoice] = Field(max_length=96)
