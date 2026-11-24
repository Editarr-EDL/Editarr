"""Strict response models for the master-set service endpoints (contract §7)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from scanner.formats.community.artifact_v3_models import ALIAS_ID, HEX64
from scanner.formats.community.master_snapshot import MasterWork
from scanner.formats.community.strict_json import MAX_SAFE_INTEGER

Token = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
Count = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
Revision = Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
Tier = Literal["1", "2", "3", "4", "5", "runtime_mismatch", "other_version"]
KeyType = Literal[
    "sha256_full",
    "sha256_sampled",
    "md5_quick",
    "audio_stream",
    "video_stream",
    "tmdb",
    "tvdb",
    "title_year",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class MatchResult(_Strict):
    version_id: Token = Field(alias="versionId")
    status: Literal["confirmed", "candidate"]
    alias_id: str | None = Field(alias="aliasId", pattern=ALIAS_ID)
    label: str | None = Field(max_length=512)
    label_text: str | None = Field(alias="labelText", max_length=512)
    work: MasterWork
    tier: Tier
    match_percent: Literal[100, 80, 65, 30, 15, 5, 0] = Field(alias="matchPercent")
    matched_keys: list[KeyType] = Field(alias="matchedKeys", max_length=8)
    runtime_agreement: Literal[
        "same_cut", "possible_cut", "outside", "unknown", "not_applicable"
    ] = Field(alias="runtimeAgreement")
    runtime_ms: int | None = Field(alias="runtimeMs", ge=1, le=86_400_000)
    trust: float = Field(ge=0, le=1, allow_inf_nan=False)
    revision: Revision
    filter_count: Count = Field(alias="filterCount")
    contributor_count: Count = Field(alias="contributorCount")


class MatchResponse(_Strict):
    version: Literal[1]
    results: list[MatchResult] = Field(max_length=50)


class Alias(_Strict):
    alias_id: str = Field(alias="aliasId", pattern=ALIAS_ID)
    label: str = Field(min_length=1, max_length=128)
    spellings: list[Annotated[str, Field(max_length=256)]] = Field(max_length=256)


class AliasesResponse(_Strict):
    version: Literal[1]
    aliases: list[Alias] = Field(max_length=1000)


class SubmissionOutcome(_Strict):
    new_filters: Count = Field(alias="newFilters")
    supports: Count
    disputes: Count
    coverage_negatives: Count = Field(alias="coverageNegatives")
    ignored: Count
    unassigned: Count


class AliasSuggestion(_Strict):
    suggestion_id: Token = Field(alias="suggestionId")
    text: str = Field(max_length=256)
    state: Literal["pending"]


class SubmissionResult(_Strict):
    version: Literal[1]
    operation_id: Token = Field(alias="operationId")
    submission_id: Token = Field(alias="submissionId")
    submission_digest: str = Field(alias="submissionDigest", pattern=HEX64)
    work_id: Token = Field(alias="workId")
    version_id: Token = Field(alias="versionId")
    version_status: Literal["confirmed", "candidate"] = Field(alias="versionStatus")
    disposition: Literal["attached", "created_confirmed", "created_candidate"]
    tier: Tier | None
    revision: Revision
    supersedes: Token | None
    outcome: SubmissionOutcome
    alias_suggestion: AliasSuggestion | None = Field(alias="aliasSuggestion")


class VoteResult(_Strict):
    version: Literal[1]
    operation_id: Token = Field(alias="operationId")
    filter_id: Token = Field(alias="filterId")
    version_id: Token = Field(alias="versionId")
    vote: Literal["up", "down", "none"]
    revision: Revision


class ReportResult(_Strict):
    version: Literal[1]
    operation_id: Token = Field(alias="operationId")
    report_id: Token = Field(alias="reportId")
    state: Literal["open"]


class OwnVote(_Strict):
    filter_id: Token = Field(alias="filterId")
    vote: Literal["up", "down"]


class OwnVotes(_Strict):
    version: Literal[1]
    version_id: Token = Field(alias="versionId")
    votes: list[OwnVote] = Field(max_length=4000)


class SyncChange(_Strict):
    version_id: Token = Field(alias="versionId")
    status: Literal["changed", "merged", "unavailable"]
    revision: Revision | None
    merged_into: Token | None = Field(default=None, alias="mergedInto")


class SyncResponse(_Strict):
    version: Literal[1]
    changed: list[SyncChange] = Field(max_length=500)
    next_run_allowed_at: Count = Field(alias="nextRunAllowedAt")


def completed(model: type[_Strict], value: object) -> _Strict:
    """Validate a mutation's `{"version":1,"state":"completed","result":{...}}` envelope."""
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "state", "result"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["state"] != "completed"
    ):
        raise ValueError("Expected a completed operation envelope")
    return model.model_validate(value["result"], strict=True)
