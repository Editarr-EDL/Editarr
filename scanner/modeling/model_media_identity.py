"""Strict local input for explicitly confirmed public work identity."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .media_identity import EntityType


class ConfirmedIdentifier(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["tmdb", "tvdb"]
    entity_type: EntityType
    value: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$")

    @model_validator(mode="after")
    def valid_provider_value(self):
        valid = self.value.isascii() and self.value.isdigit() and int(self.value) > 0
        if not valid:
            raise ValueError("Enter a positive numeric TMDB or TVDB ID")
        return self


class ConfirmedEpisodeNumber(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scheme: Literal["aired", "absolute", "dvd", "anime"]
    season: StrictInt | None = Field(default=None, ge=0)
    number: StrictInt = Field(ge=0)
