import json
from pathlib import Path

import httpx
import pytest

from scanner.integrations.jellyfin import JellyfinClient
from scanner.integrations.media_identity_capture import MediaCaptureSanitizer
from scanner.modeling.media_identity import (
    AudioEvidence,
    DurationEvidence,
    EpisodeNumber,
    ProviderIdentifier,
    ReleaseEvidence,
    known_release_conflicts,
    normalize_jellyfin,
    normalize_manual,
    provider_identifiers,
)


def test_a_series_id_is_not_an_episode_identifier():
    assert provider_identifiers("episode", {"tvdb": 1234}, source="jellyfin.episode") == ()
    identifiers = provider_identifiers("series", {"tvdb": 1234}, source="jellyfin.series")
    assert [(item.provider, item.entity_type, item.value) for item in identifiers] == [
        ("tvdb", "series", "1234")
    ]
    assert identifiers[0].provenance == "jellyfin.series"


def test_real_sanitized_jellyfin_captures():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/community/media-identity-captures-v1.json").read_text()
    )
    assert fixture["origin"] == "live_provider_responses"
    records = fixture["providers"]["jellyfin"]
    result = normalize_jellyfin(
        records["episode"],
        series=records["series"],
        source=records["episode"]["MediaSources"][0],
    )
    assert result.episode_numbers == (EpisodeNumber("aired", 0, 1),)
    assert result.identifiers == ()
    assert result.release.duration.source == "jellyfin_ticks"
    assert normalize_jellyfin(records["movie"]).identifiers
    assert result.input_requirement is None


def test_capture_sanitizer_excludes_private_fields_and_preserves_id_equality():
    sanitizer = MediaCaptureSanitizer()
    series = sanitizer.jellyfin({"Id": "private-series", "Type": "Series", "Name": "Private"})
    episode = sanitizer.jellyfin(
        {"Id": "private-episode", "SeriesId": "private-series", "Type": "Episode"}
    )
    assert episode["SeriesId"] == series["Id"] != episode["Id"]
    jellyfin = sanitizer.jellyfin(
        {
            "Id": "private-local-id",
            "Name": "Private title",
            "Type": "Movie",
            "ProviderIds": {"Tmdb": "800", "PrivateProvider": "secret"},
            "MediaSources": [
                {"Path": "/private/movie.mkv", "Name": "Private.Release", "RunTimeTicks": 12}
            ],
        }
    )
    assert "Private" not in str(jellyfin)
    assert "/private" not in str(jellyfin)
    assert "secret" not in str(jellyfin)
    assert jellyfin["MediaSources"][0]["RunTimeTicks"] == 12


@pytest.mark.parametrize("available", [True, False])
def test_jellyfin_discovery_has_typed_version_and_optional_series_context(available):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/community/media-identity-captures-v1.json").read_text()
    )["providers"]["jellyfin"]

    def handler(request):
        if request.url.params.get("Ids") == fixture["series"]["Id"]:
            return httpx.Response(200 if available else 503, json={"Items": [fixture["series"]]})
        return httpx.Response(200, json={"Items": [fixture["episode"]]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        client = JellyfinClient("https://jellyfin.test", "secret", transport)
        result = client.item(fixture["episode"]["Id"])
    assert result.media_identity.identifiers == ()
    assert bool(result.media_identity.series_identifiers) is available
    assert (result.media_identity.input_requirement is None) is available
    assert result.versions[0].media_identity.release.size_bytes == 156830326


def test_invalid_or_local_ids_do_not_become_public_aliases():
    assert (
        provider_identifiers(
            "movie", {"tmdb": 0, "imdb": "/private/movie", "id": 42}, source="jellyfin.movie"
        )
        == ()
    )


def test_jellyfin_episode_id_remains_unverified_without_series_context():
    item = {
        "Type": "Episode",
        "Id": "local-episode",
        "SeriesId": "local-series",
        "ProviderIds": {"Tvdb": "901"},
        "ParentIndexNumber": 1,
        "IndexNumber": 3,
        "RunTimeTicks": 250000000,
    }
    series = {"Type": "Series", "Id": "local-series", "ProviderIds": {"Tvdb": "800"}}
    result = normalize_jellyfin(
        item,
        series=series,
        source={
            "Path": "/private/episode.mkv",
            "RunTimeTicks": 240000000,
        },
    )
    assert result.identifiers == ()
    assert result.series_identifiers == (
        ProviderIdentifier("tvdb", "series", "800", "jellyfin.series"),
    )
    assert result.input_requirement is None
    assert normalize_jellyfin(item).input_requirement is not None
    assert (
        normalize_jellyfin(item, series={**series, "Id": "different-series"}).series_identifiers
        == ()
    )
    assert result.release.duration == DurationEvidence(24, "jellyfin_ticks", 0.0000001)
    assert EpisodeNumber("absolute", None, 3) != result.episode_numbers[0]
    assert EpisodeNumber("dvd", 1, 3) != result.episode_numbers[0]
    assert "/private" not in repr(result)


def test_manual_confirmation_never_infers_identity_from_filename():
    result = normalize_jellyfin({"Type": "Movie", "ProviderIds": {"Imdb": "tt0000501"}})
    assert result.identifiers == ()
    assert result.input_requirement is not None
    release = ReleaseEvidence(
        scene_name="Movie.2025-GROUP", duration=DurationEvidence(120, "file_probe")
    )
    assert normalize_manual("movie", release=release).input_requirement is not None
    alias = ProviderIdentifier("tmdb", "movie", "501", "user_confirmed")
    result = normalize_manual("movie", (alias,), release=release, confirmed=True)
    assert result.input_requirement is None
    assert result.release == release
    with pytest.raises(ValueError, match="wrong entity type"):
        normalize_manual("episode", (alias,), confirmed=True)


def test_scene_name_cannot_override_known_duration_or_audio_conflicts():
    left = ReleaseEvidence(
        scene_name="Example-GROUP",
        duration=DurationEvidence(120, "file_probe"),
        audio=AudioEvidence("en", "main"),
    )
    right = ReleaseEvidence(
        scene_name="Example-GROUP",
        duration=DurationEvidence(140, "file_probe"),
        audio=AudioEvidence("fr", "commentary"),
    )
    assert known_release_conflicts(left, right, duration_tolerance_seconds=5) == (
        "measured_duration",
        "audio_language",
        "audio_role",
    )
    assert "measured_duration" not in known_release_conflicts(
        left,
        ReleaseEvidence(duration=DurationEvidence(140, "jellyfin_ticks", 60)),
        duration_tolerance_seconds=5,
    )


def test_jellyfin_movie_and_missing_episode_context():
    movie = normalize_jellyfin(
        {"Type": "Movie", "Id": "local-id", "ProviderIds": {"Tmdb": "501"}},
        source={"SceneName": "Film.2025-GROUP", "Path": "/private/film.mkv"},
    )
    assert movie.identifiers == (ProviderIdentifier("tmdb", "movie", "501", "jellyfin.item"),)
    assert movie.input_requirement is None
    assert normalize_jellyfin({"Type": "Episode", "IndexNumber": 3}).input_requirement


def test_manual_alternative_numbering_and_path_rejection():
    series = ProviderIdentifier("tvdb", "series", "800", "user_confirmed")
    absolute = EpisodeNumber("absolute", None, 12)
    dvd = EpisodeNumber("dvd", 1, 3)
    result = normalize_manual(
        "episode",
        series_identifiers=(series,),
        episode_numbers=(absolute, dvd),
        confirmed=True,
    )
    assert result.input_requirement is None
    assert absolute != dvd
    with pytest.raises(ValueError, match="local paths"):
        ReleaseEvidence(scene_name="/private/movies/film.mkv")
    with pytest.raises(ValueError, match="public identifier"):
        ProviderIdentifier("tmdb", "movie", "/private/film.mkv", "user_confirmed")
