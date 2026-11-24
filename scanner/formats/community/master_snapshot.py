"""Strict parser for service-generated `community-master-snapshot-1` bytes (§7.3)."""

from typing import Literal

from pydantic import Field, ValidationError

from scanner.modeling.model_core import MCF_CATEGORY_TO_FAMILY

from .artifact_v3_models import (
    ALIAS_ID,
    MAX_DURATION_MS,
    PROVIDER_ID,
    SafeInt,
    StrictCamelModel,
    Token,
)
from .strict_json import MAX_SAFE_INTEGER, StrictJsonError, parse_strict_json

MASTER_SNAPSHOT_SCHEMA = "community-master-snapshot-1"
MAX_SNAPSHOT_BYTES = 2_000_000
DEFAULT_MAX_FILTERS = 4_000
_CATEGORIES = frozenset(MCF_CATEGORY_TO_FAMILY.values())


class MasterSnapshotError(ValueError):
    """Snapshot bytes are not a valid master snapshot."""


class MasterWork(StrictCamelModel):
    work_id: Token
    kind: Literal["movie", "episode"]
    tmdb: str | None = Field(pattern=PROVIDER_ID)
    tvdb: str | None = Field(pattern=PROVIDER_ID)
    season: int | None = Field(ge=0, le=9999)
    episode: int | None = Field(ge=0, le=9999)
    last_episode: int | None = Field(ge=0, le=9999 + 15)
    title: str = Field(min_length=1)
    year: int | None = Field(ge=1870, le=2200)


class VersionInfo(StrictCamelModel):
    status: Literal["confirmed", "candidate"]
    alias_id: str | None = Field(pattern=ALIAS_ID)
    label: str | None
    label_text: str | None
    runtime_ms: int | None = Field(ge=1, le=MAX_DURATION_MS)
    trust: float = Field(ge=0, le=1, allow_inf_nan=False)


class Spread(StrictCamelModel):
    start_ms: SafeInt
    end_ms: SafeInt


class Support(StrictCamelModel):
    attestations: SafeInt
    upvotes: SafeInt


class Negative(StrictCamelModel):
    downvotes: SafeInt
    disputes: SafeInt
    coverage_negatives: SafeInt


class MasterFilter(StrictCamelModel):
    filter_id: Token
    merged_ids: list[Token]
    category: str
    subcategory: str
    severity: Literal["low", "medium", "high"]
    action: Literal["skip", "mute"]
    start_ms: SafeInt
    end_ms: SafeInt
    spread: Spread
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    contributor_count: SafeInt
    support: Support
    negative: Negative
    low_confidence: bool


class MasterSnapshot(StrictCamelModel):
    schema_version: Literal["community-master-snapshot-1"]
    service: str = Field(min_length=1, max_length=256)
    version_id: Token
    revision: int = Field(ge=1, le=MAX_SAFE_INTEGER)
    generated_at: SafeInt
    work: MasterWork
    version_info: VersionInfo
    filters: list[MasterFilter]


def _check_work(work: MasterWork) -> None:
    numbering = (work.season, work.episode, work.last_episode)
    if work.tmdb is None and work.tvdb is None:
        raise MasterSnapshotError("Snapshot work has no TMDB or TVDB identifier")
    if work.kind == "movie" and numbering != (None, None, None):
        raise MasterSnapshotError("Snapshot movie has episode numbering")
    if work.kind == "episode" and (
        work.season is None
        or work.episode is None
        or (
            work.last_episode is not None
            and not work.episode <= work.last_episode <= work.episode + 15
        )
    ):
        raise MasterSnapshotError("Snapshot episode numbering is invalid")


def _filter_order(item: MasterFilter) -> tuple:
    return (item.start_ms, item.end_ms, item.category.encode(), item.filter_id.encode())


def _check_filters(filters: list[MasterFilter], max_filters: int) -> None:
    if len(filters) > max_filters:
        raise MasterSnapshotError("Snapshot exceeds the served filter limit")
    if [_filter_order(item) for item in filters] != sorted(map(_filter_order, filters)):
        raise MasterSnapshotError("Snapshot filters are not in canonical order")
    seen: set[str] = set()
    for item in filters:
        ids = [item.filter_id, *item.merged_ids]
        if seen.intersection(ids) or len(set(ids)) != len(ids):
            raise MasterSnapshotError("Snapshot filter IDs must be unique")
        seen.update(ids)
        if item.category not in _CATEGORIES or (
            MCF_CATEGORY_TO_FAMILY.get(item.subcategory) != item.category
        ):
            raise MasterSnapshotError("Snapshot filter taxonomy is invalid")
        if not item.start_ms < item.end_ms:
            raise MasterSnapshotError("Snapshot filter bounds are invalid")
        if item.contributor_count != item.support.attestations:
            raise MasterSnapshotError("contributorCount must equal support.attestations")


def parse_master_snapshot(
    raw: bytes, *, max_filters: int = DEFAULT_MAX_FILTERS
) -> MasterSnapshot:
    """Strictly parse exact snapshot bytes; callers verify the digest and signature first."""
    try:
        value = parse_strict_json(raw, max_bytes=MAX_SNAPSHOT_BYTES)
        snapshot = MasterSnapshot.model_validate(value, strict=True, by_alias=True, by_name=False)
    except (StrictJsonError, ValidationError) as exc:
        raise MasterSnapshotError(f"Invalid {MASTER_SNAPSHOT_SCHEMA}: {exc}") from exc
    _check_work(snapshot.work)
    _check_filters(snapshot.filters, max_filters)
    return snapshot
