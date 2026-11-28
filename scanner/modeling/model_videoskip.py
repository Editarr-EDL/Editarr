"""Metadata preserved from an imported VideoSkip file."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from .model_core import ReviewWriteModel


class VideoSkipAttribution(ReviewWriteModel):
    """Optional publisher information supplied alongside a downloaded file."""

    source_url: str | None = Field(default=None, max_length=512)
    uploader: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value):
        if value is not None:
            url = urlsplit(value)
            if (
                url.scheme != "https"
                or url.netloc not in {
                    "videoskip.org", "www.videoskip.org", "videoskip.herokuapp.com",
                }
                or not re.fullmatch(r"/exchange/skip/[0-9]+/?", url.path)
                or url.query
                or url.fragment
            ):
                raise ValueError("Source URL must point to a VideoSkip Exchange skip file")
        return value

    @field_validator("uploader")
    @classmethod
    def validate_uploader(cls, value):
        if value is not None and (not value.strip() or not value.isprintable()):
            raise ValueError("Uploader must be a single printable name")
        return value.strip() if value is not None else None


class VideoSkipSync(BaseModel):
    reference_seconds: float | None = Field(default=None, allow_inf_nan=False)
    reference_description: str | None = None
    platform_offsets_seconds: dict[str, float] = Field(default_factory=dict)
    screenshot_media_type: str | None = None
    screenshot_sha256: str | None = None
    notes: list[str] = Field(default_factory=list)


class VideoSkipSource(VideoSkipAttribution):
    format: Literal["videoskip"] = "videoskip"
    filename: str
    sha256: str
    sync: VideoSkipSync


class VideoSkipImportIssue(BaseModel):
    code: str
    message: str
    line_number: int = Field(gt=0)
