"""Community work identity (§3.1, §5.2) from a local confirmed media identity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .community_match_keys import ProviderKey, TitleYearKey
from .community_normalization import normalize
from .media_identity import MediaIdentity

_PROVIDER_ID = re.compile(r"[0-9]{1,12}")
_MAX_SPAN = 15


class WorkIdentityRequired(ValueError):
    """The media has no usable TMDB or TVDB identity; `code` is the contract error."""

    code = "work_identity_required"


@dataclass(frozen=True)
class WorkIdentity:
    """A movie, or a series episode identified by its series ID plus numbering."""

    kind: Literal["movie", "episode"]
    tmdb: str | None
    tvdb: str | None
    season: int | None
    episode: int | None
    last_episode: int | None
    title: str
    year: int | None

    def as_artifact(self) -> dict:
        """The artifact `work` object, in contract key order."""
        return {
            "kind": self.kind,
            "tmdb": self.tmdb,
            "tvdb": self.tvdb,
            "season": self.season,
            "episode": self.episode,
            "lastEpisode": self.last_episode,
            "title": self.title,
            "year": self.year,
        }

    def provider_keys(self) -> tuple[ProviderKey, ...]:
        numbering = (
            {"season": self.season, "episode": self.episode, "last_episode": self.last_episode}
            if self.kind == "episode"
            else {}
        )
        return tuple(
            ProviderKey(provider, self.kind, value, **numbering)
            for provider, value in (("tmdb", self.tmdb), ("tvdb", self.tvdb))
            if value is not None
        )


def _ids(identifiers, entity_type: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for item in identifiers:
        if item.entity_type == entity_type and _PROVIDER_ID.fullmatch(item.value):
            found.setdefault(item.provider, item.value)
    return found


def _year(year: int | None) -> int | None:
    return year if isinstance(year, int) and 1870 <= year <= 2200 else None


def work_identity(identity: MediaIdentity | None, *, title: str, year: int | None) -> WorkIdentity:
    """Build the work for an artifact or match query.

    A movie needs a movie TMDB/TVDB ID; an episode needs a series ID plus aired
    season and episode numbers (a multi-episode span sets `lastEpisode`). Episode-level
    provider IDs are not series IDs and are never used (§3.1). Raises
    `WorkIdentityRequired` otherwise, or when the title normalizes to nothing.
    """
    if identity is None or not normalize(title or ""):
        raise WorkIdentityRequired("A TMDB or TVDB identity and a title are required")
    title = title.strip()
    if identity.entity_type == "movie":
        ids = _ids(identity.identifiers, "movie")
        if not ids:
            raise WorkIdentityRequired("A movie TMDB or TVDB ID is required")
        return WorkIdentity(
            "movie", ids.get("tmdb"), ids.get("tvdb"), None, None, None, title, _year(year)
        )
    if identity.entity_type != "episode":
        raise WorkIdentityRequired("Only movies and episodes can be shared")
    ids = _ids(identity.series_identifiers, "series")
    aired = [item for item in identity.episode_numbers if item.scheme == "aired"]
    if not ids or not aired or aired[0].season is None or aired[0].season > 9999:
        raise WorkIdentityRequired("An episode needs a series ID and aired numbering")
    first = aired[0]
    span = [
        item.number
        for item in identity.episode_span
        if item.scheme == "aired" and item.season == first.season
    ]
    last = max(span) if span else None
    if last is not None and not first.number < last <= first.number + _MAX_SPAN:
        last = None
    if first.number > 9999:
        raise WorkIdentityRequired("Episode numbering is out of range")
    return WorkIdentity(
        "episode",
        ids.get("tmdb"),
        ids.get("tvdb"),
        first.season,
        first.number,
        last,
        title,
        _year(year),
    )


def title_key(title: str | None, year: int | None) -> TitleYearKey | None:
    """The tier 5 key, or None when the title normalizes to nothing or the year is unknown."""
    if not title or not normalize(title) or _year(year) is None:
        return None
    return TitleYearKey(title.strip(), year)
