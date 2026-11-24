"""Find community versions for a local file: `/v1/match` plus a local Match % cross-check.

The server ranks every version sharing a key with the query (§4). The client cannot
see version keys, so it rebuilds each result from the keys the server says matched
and the served work, re-scores it with the same ladder and refuses any response whose
tiers, Match % or order disagree (`invalid_response`).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from scanner.modeling.community_match_keys import (
    HashKey,
    MatchKey,
    ProviderKey,
    StreamKey,
    TitleYearKey,
    key_tier,
)
from scanner.modeling.community_match_ladder import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MatchQuery,
    VersionCandidate,
    rank_versions,
    score_version,
)
from scanner.modeling.community_normalization import (
    Alias,
    ResolvedLabel,
    resolve_query_label,
)

from .find_targets import FindKeys
from .master_service import MasterService
from .master_service_models import MatchResult
from .transport import CommunityFailure

FILE_TIERS = frozenset({"1", "2", "3"})


def alignment_required(tier: str | None) -> bool:
    """Tiers 1–3 apply directly; every fuzzier tier needs local alignment first (D21)."""
    return tier not in FILE_TIERS


def key_type(key: MatchKey) -> str:
    """The `/v1/match` key type name of a key."""
    return "title_year" if isinstance(key, TitleYearKey) else key.type


def _key_json(key: MatchKey) -> dict:
    if isinstance(key, HashKey):
        return {"type": key.type, "value": key.value, "sizeBytes": key.size_bytes}
    if isinstance(key, StreamKey):
        return {"type": key.type, "algorithm": key.algorithm, "value": key.value}
    if isinstance(key, TitleYearKey):
        return {"type": "title_year", "title": key.title, "year": key.year}
    document = {"type": key.type, "kind": key.kind, "id": key.id}
    if key.kind == "episode":
        document |= {"season": key.season, "episode": key.episode}
        if key.last_episode is not None:
            document["lastEpisode"] = key.last_episode
    return document


def query_keys(keys: FindKeys) -> tuple[MatchKey, ...]:
    """Every available key, file keys first; at least one is required."""
    found: list[MatchKey] = [*keys.file_keys]
    if keys.work is not None:
        found.extend(keys.work.provider_keys())
    if keys.title_year is not None:
        found.append(keys.title_year)
    if not found:
        raise CommunityFailure("match_keys_unavailable")
    return tuple(found)


def label_body(label: Mapping | None) -> dict | None:
    """The `/v1/match` label: `{aliasId}`, `{text}` or null (Theatrical)."""
    if label is None:
        return None
    if set(label) == {"alias_id"} and isinstance(label["alias_id"], str):
        return {"aliasId": label["alias_id"]}
    if set(label) == {"text"} and (label["text"] is None or isinstance(label["text"], str)):
        return {"text": label["text"]}
    raise CommunityFailure("invalid_match")


def match_body(keys: FindKeys, label: Mapping | None, limit: int = DEFAULT_LIMIT) -> dict:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise CommunityFailure("invalid_match")
    return {
        "version": 1,
        "keys": [_key_json(key) for key in query_keys(keys)],
        "label": label_body(label),
        "runtimeMs": keys.runtime_ms,
        "limit": limit,
    }


def _candidate(result: MatchResult, query: MatchQuery) -> VersionCandidate:
    """Rebuild a version from what the server says matched, for local re-scoring."""
    matched = set(result.matched_keys)
    file_keys = tuple(
        key
        for key in query.keys
        if isinstance(key, (HashKey, StreamKey))
        and key.type in matched
        and result.tier in FILE_TIERS
    )
    work = result.work
    numbering = (
        {"season": work.season, "episode": work.episode, "last_episode": work.last_episode}
        if work.kind == "episode"
        else {}
    )
    work_keys: list = [
        ProviderKey(provider, work.kind, value, **numbering)
        for provider, value in (("tmdb", work.tmdb), ("tvdb", work.tvdb))
        if value is not None
    ]
    if "title_year" in matched:
        # A work has several title keys but serves one title; trust the claimed match.
        work_keys.extend(key for key in query.keys if isinstance(key, TitleYearKey))
    return VersionCandidate(
        result.version_id,
        result.status,
        ResolvedLabel(result.alias_id, result.label_text),
        result.runtime_ms,
        result.trust,
        file_keys,
        tuple(work_keys),
    )


def cross_check(query: MatchQuery, results: Iterable[MatchResult]) -> None:
    """Refuse results whose tier, Match %, keys, runtime agreement or order differ locally."""
    results = list(results)
    query_types = {
        "title_year" if isinstance(key, TitleYearKey) else key.type for key in query.keys
    }
    candidates = []
    for result in results:
        if not set(result.matched_keys) <= query_types:
            raise CommunityFailure("invalid_response")
        candidate = _candidate(result, query)
        score = score_version(query, candidate)
        if score is None or (
            score.tier,
            score.match_percent,
            score.matched_keys,
            score.runtime_agreement,
        ) != (
            result.tier,
            result.match_percent,
            tuple(result.matched_keys),
            result.runtime_agreement,
        ):
            raise CommunityFailure("invalid_response")
        candidates.append(candidate)
    if not candidates:
        return
    ranked = rank_versions(query, candidates, limit=min(len(candidates), MAX_LIMIT))
    if [version.version_id for _score, version in ranked] != [r.version_id for r in results]:
        raise CommunityFailure("invalid_response")


@dataclass(frozen=True)
class FoundVersion:
    result: MatchResult

    @property
    def alignment_required(self) -> bool:
        return alignment_required(self.result.tier)

    def as_json(self, linked_link_id: str | None = None) -> dict:
        """One local API Find result (§2)."""
        result = self.result
        return {
            "communityVersionId": result.version_id,
            "label": result.label,
            "aliasId": result.alias_id,
            "status": result.status,
            "unconfirmed": result.status == "candidate",
            "tier": result.tier,
            "matchPercent": result.match_percent,
            "runtimeAgreement": result.runtime_agreement,
            "filterCount": result.filter_count,
            "contributorCount": result.contributor_count,
            "trust": result.trust,
            "revision": result.revision,
            "alignmentRequired": self.alignment_required,
            "linkedLinkId": linked_link_id,
        }


async def find_versions(
    service: MasterService,
    keys: FindKeys,
    *,
    label: Mapping | None = None,
    limit: int = DEFAULT_LIMIT,
    aliases: tuple[Alias, ...] | None = None,
) -> tuple[FoundVersion, ...]:
    """Query `/v1/match` with every available key and cross-check the ranking locally.

    A `{text}` label needs the approved aliases to resolve; they are fetched when not
    supplied.
    """
    body = match_body(keys, label, limit)
    if body["label"] is not None and "text" in body["label"] and aliases is None:
        aliases = await service.aliases()
    query = MatchQuery(
        tuple(sorted(query_keys(keys), key=key_tier)),
        resolve_query_label(body["label"], aliases or ()),
        keys.runtime_ms,
    )
    results = await service.match(body)
    cross_check(query, results)
    return tuple(FoundVersion(result) for result in results)
