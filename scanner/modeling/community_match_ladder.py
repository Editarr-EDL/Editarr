"""Community match ladder and Match % (§4), identical to the service's `/v1/match` scoring."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .community_match_keys import (
    KEY_TYPE_ORDER,
    FileKey,
    HashKey,
    MatchKey,
    ProviderKey,
    StreamKey,
    TitleYearKey,
    key_tier,
    keys_equal,
)
from .community_normalization import THEATRICAL, ResolvedLabel, labels_equal

Tier = Literal["1", "2", "3", "4", "5", "runtime_mismatch", "other_version"]
RuntimeAgreement = Literal["same_cut", "possible_cut", "outside", "unknown", "not_applicable"]
VersionStatus = Literal["confirmed", "candidate"]

SAME_CUT_MS = 2_000
POSSIBLE_CUT_MS = 30_000
MATCH_PERCENT: dict[str, int] = {
    "1": 100,
    "2": 80,
    "3": 65,
    "4": 30,
    "5": 15,
    "runtime_mismatch": 5,
    "other_version": 0,
}
_RUNTIME_ORDER = ("same_cut", "possible_cut", "not_applicable", "unknown", "outside")
DEFAULT_LIMIT = 20
MAX_LIMIT = 50


@dataclass(frozen=True)
class MatchQuery:
    """A `/v1/match` query: keys, a resolved label (Theatrical when absent) and runtime."""

    keys: tuple[MatchKey, ...]
    label: ResolvedLabel = ResolvedLabel(THEATRICAL, None)
    runtime_ms: int | None = None


@dataclass(frozen=True)
class VersionCandidate:
    """A non-merged, unquarantined version with its tier 1–3 keys and its work's keys."""

    version_id: str
    status: VersionStatus
    label: ResolvedLabel
    runtime_ms: int | None
    trust: float
    file_keys: tuple[FileKey, ...]
    work_keys: tuple[ProviderKey | TitleYearKey, ...]


@dataclass(frozen=True)
class MatchScore:
    """The best ladder tier for one version."""

    version_id: str
    tier: Tier
    match_percent: int
    matched_keys: tuple[str, ...]
    runtime_agreement: RuntimeAgreement


def runtime_agreement(query_ms: int | None, version_ms: int | None) -> RuntimeAgreement:
    """Classify the runtime difference (±2 s same cut, ±30 s possible cut, else outside)."""
    if query_ms is None or version_ms is None:
        return "unknown"
    difference = abs(query_ms - version_ms)
    if difference <= SAME_CUT_MS:
        return "same_cut"
    return "possible_cut" if difference <= POSSIBLE_CUT_MS else "outside"


def _ordered_types(types: set[str]) -> tuple[str, ...]:
    return tuple(key_type for key_type in KEY_TYPE_ORDER if key_type in types)


def _key_type(key: MatchKey) -> str:
    return "title_year" if isinstance(key, TitleYearKey) else key.type


def _file_tier(query: MatchQuery, version: VersionCandidate) -> tuple[int, set[str]] | None:
    hits: dict[int, set[str]] = {}
    for key in query.keys:
        if isinstance(key, (HashKey, StreamKey)) and any(
            keys_equal(key, other) for other in version.file_keys
        ):
            hits.setdefault(key_tier(key), set()).add(key.type)
    if not hits:
        return None
    best = min(hits)
    return best, hits[best]


def score_version(query: MatchQuery, version: VersionCandidate) -> MatchScore | None:
    """Return the best tier for a version, or None when it shares no key with the query."""
    file_hit = _file_tier(query, version)
    if file_hit is not None:
        tier, types = file_hit
        return MatchScore(
            version.version_id,
            str(tier),  # type: ignore[arg-type]
            MATCH_PERCENT[str(tier)],
            _ordered_types(types),
            "not_applicable",
        )
    identity: dict[int, set[str]] = {}
    for key in query.keys:
        if isinstance(key, (ProviderKey, TitleYearKey)) and any(
            type(key) is type(other) and keys_equal(key, other) for other in version.work_keys
        ):
            identity.setdefault(key_tier(key), set()).add(_key_type(key))
    if not identity:
        return None
    all_types = _ordered_types(set().union(*identity.values()))
    agreement = runtime_agreement(query.runtime_ms, version.runtime_ms)
    if not labels_equal(query.label, version.label):
        return MatchScore(version.version_id, "other_version", 0, all_types, agreement)
    if agreement in ("same_cut", "possible_cut"):
        best = min(identity)
        tier: Tier = "4" if best == 4 else "5"
        return MatchScore(
            version.version_id,
            tier,
            MATCH_PERCENT[tier],
            _ordered_types(identity[best]),
            agreement,
        )
    return MatchScore(version.version_id, "runtime_mismatch", 5, all_types, agreement)


def _sort_key(item: tuple[MatchScore, VersionCandidate]) -> tuple:
    score, version = item
    return (
        -score.match_percent,
        0 if version.status == "confirmed" else 1,
        _RUNTIME_ORDER.index(score.runtime_agreement),
        -version.trust,
        version.version_id.encode("ascii"),
    )


def rank_versions(
    query: MatchQuery, versions: Iterable[VersionCandidate], *, limit: int = DEFAULT_LIMIT
) -> list[tuple[MatchScore, VersionCandidate]]:
    """Score versions sharing a key with the query, order them per §4 and truncate."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError("limit must be 1–50")
    scored = [
        (score, version)
        for version in versions
        if (score := score_version(query, version)) is not None
    ]
    return sorted(scored, key=_sort_key)[:limit]
