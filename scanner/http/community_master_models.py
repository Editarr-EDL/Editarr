"""Strict snake_case request bodies of the master-set routes (local API §2–§9).

Extra fields are a 422 validation list.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Path
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from scanner.integrations.community.find_targets import FindTarget
from scanner.modeling.community_master_alignment import MAX_ANCHORS

TOKEN = r"^[A-Za-z0-9_.-]{1,128}$"
Token = Annotated[str, Field(pattern=TOKEN)]
LinkId = Annotated[str, Path(pattern=TOKEN, max_length=128)]
FilterId = Annotated[str, Path(pattern=TOKEN, max_length=128)]
Milliseconds = Annotated[StrictInt, Field(ge=0, le=86_400_000)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Empty(_Body):
    """`{}` for actions that take no input."""


class AliasLabel(_Body):
    alias_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")


class TextLabel(_Body):
    text: str | None = Field(max_length=512)


class FindRequest(_Body):
    target: FindTarget
    label: AliasLabel | TextLabel | None = None
    refresh: StrictBool = False


class ApplyRequest(_Body):
    target: FindTarget
    find_key: str = Field(min_length=1, max_length=128)
    community_version_id: Token
    confirmed: StrictBool
    replace: StrictBool = False


class Anchor(_Body):
    filter_id: Token
    local_start_ms: Milliseconds


class AlignmentPreviewRequest(_Body):
    """Exactly one of `anchors` (1–50) or `offset_ms`."""

    anchors: list[Anchor] | None = Field(default=None, min_length=1, max_length=MAX_ANCHORS)
    offset_ms: Annotated[StrictInt, Field(ge=-86_400_000, le=86_400_000)] | None = None
    audio_track_index: Annotated[StrictInt, Field(ge=0, le=4095)] | None = None

    @model_validator(mode="after")
    def one_method(self):
        if (self.anchors is None) == (self.offset_ms is None):
            raise ValueError("Send either anchors or offset_ms")
        return self


class AlignmentApproval(_Body):
    token: str = Field(min_length=1, max_length=128)
    for_revision: Annotated[StrictInt, Field(ge=1)]
    confirmed: StrictBool


Unit = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False, strict=True)]


class PreferencesRequest(_Body):
    """Strict JSON numbers and booleans; a string or boolean threshold is refused."""

    confidence_threshold: Unit | None = None
    show_low_confidence: StrictBool | None = None
    hide_rejected_downvoted: StrictBool | None = None


class VoteRequest(_Body):
    vote: Literal["up", "down", "none"]
    operation_id: str = Field(pattern=r"^vote-[A-Za-z0-9._~-]{1,123}$")
    confirmed: StrictBool


class VoteRetry(_Body):
    operation_id: str = Field(pattern=r"^vote-[A-Za-z0-9._~-]{1,123}$")
    confirmed: StrictBool


class ReportTarget(_Body):
    type: Literal["filter", "version"]
    id: Token


class ReportRequest(_Body):
    target: ReportTarget
    reason: Literal["spam", "inaccurate", "privacy", "rights", "other"]
    note: str | None = Field(default=None, max_length=1000)
    confirmed: StrictBool


class NotificationsRead(_Body):
    """Exactly one of `notification_ids` (at most 100) or `all: true`."""

    notification_ids: list[Annotated[str, Field(max_length=64)]] | None = Field(
        default=None, max_length=100
    )
    all: Literal[True] | None = None

    @model_validator(mode="after")
    def one_selection(self):
        if (self.notification_ids is None) == (self.all is None):
            raise ValueError("Send either notification_ids or all")
        return self
