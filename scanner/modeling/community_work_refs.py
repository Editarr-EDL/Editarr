"""Normalize explicit TMDB/TVDB donation associations without provider requests."""

import re
from urllib.parse import parse_qs, urlsplit

from .media_identity import EpisodeNumber, MediaIdentity, ProviderIdentifier, normalize_manual
from .model_media_identity import ConfirmedIdentifier


def identity_from_reference(provider: str, entity_type: str, reference: str) -> MediaIdentity:
    """Accept a positive typed ID or an exact supported public provider URL."""
    value = reference.strip()
    series = ()
    numbers = ()
    if not value.isascii() or not value.isdigit():
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.fragment
        ):
            raise ValueError("Enter a supported public TMDB or TVDB HTTPS URL")
        host = (parsed.hostname or "").removeprefix("www.")
        if provider == "tmdb" and host == "themoviedb.org" and not parsed.query:
            episode = re.fullmatch(r"/tv/([0-9]+)/season/([0-9]+)/episode/([0-9]+)/?", parsed.path)
            if episode and entity_type == "episode":
                series = (ProviderIdentifier("tmdb", "series", episode[1], "user_confirmed"),)
                numbers = (EpisodeNumber("aired", int(episode[2]), int(episode[3])),)
                value = None
            else:
                expected = (
                    "movie" if entity_type == "movie" else "tv" if entity_type == "series" else None
                )
                match = re.fullmatch(r"/(movie|tv)/([0-9]+)(?:-[^/]+)?/?", parsed.path)
                if not match or match[1] != expected:
                    raise ValueError("Provider URL does not identify the selected entity type")
                value = match[2]
        elif provider == "tvdb" and host == "thetvdb.com":
            match = re.fullmatch(r"/dereferrer/(movie|series|episode)/([0-9]+)/?", parsed.path)
            query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
            if match and not query and match[1] == entity_type:
                value = match[2]
            elif (
                parsed.path in {"", "/"}
                and set(query) == {"tab", "id"}
                and query["tab"] == [entity_type]
                and len(query["id"]) == 1
            ):
                value = query["id"][0]
            else:
                raise ValueError("Enter a numeric TVDB ID or supported ID-bearing URL")
        else:
            raise ValueError("Only supported TMDB and TVDB references are accepted")
    identifiers = ()
    if value is not None:
        item = ConfirmedIdentifier(provider=provider, entity_type=entity_type, value=value)
        identifiers = (ProviderIdentifier(**item.model_dump(), provenance="user_confirmed"),)
    return normalize_manual(
        entity_type, identifiers, series_identifiers=series, episode_numbers=numbers, confirmed=True
    )
