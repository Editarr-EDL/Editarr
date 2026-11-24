"""Find: `/v1/match` queries from local keys, local Match % cross-checks and tier gating."""

import asyncio

import pytest

from scanner.integrations.community.find import (
    alignment_required,
    cross_check,
    find_versions,
    match_body,
)
from scanner.integrations.community.find_targets import FindKeys
from scanner.integrations.community.master_service import MasterService
from scanner.integrations.community.master_service_models import MatchResult
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_match_keys import HashKey, StreamKey
from scanner.modeling.community_match_ladder import MatchQuery
from scanner.modeling.community_normalization import ResolvedLabel
from scanner.modeling.community_work_identity import (
    WorkIdentityRequired,
    title_key,
    work_identity,
)
from scanner.modeling.media_identity import EpisodeNumber, MediaIdentity, ProviderIdentifier
from tests.community_master_service_support import FakeService, connect
from tests.test_community_master_service import MATCH

MD5 = HashKey("md5_quick", "a" * 32, 4_823_749_120)
VIDEO = StreamKey("video_stream", "editarr-community-video-sha256-sampled-1", "b" * 64)
MOVIE = MediaIdentity("movie", (ProviderIdentifier("tmdb", "movie", "603", "jellyfin.item"),))


def keys(*file_keys, runtime_ms=8_160_512):
    return FindKeys(
        tuple(file_keys),
        work_identity(MOVIE, title="The Matrix", year=1999),
        title_key("The Matrix", 1999),
        runtime_ms,
    )


def result(**changes):
    return MatchResult.model_validate({**MATCH, **changes}, strict=True)


def query(found: FindKeys, label=ResolvedLabel("theatrical", None)):
    body = match_body(found, None)
    from scanner.integrations.community.find import query_keys

    return body, MatchQuery(query_keys(found), label, found.runtime_ms)


def test_match_body_carries_every_available_key_and_label():
    body = match_body(keys(MD5, VIDEO), {"alias_id": "extended"}, limit=5)
    assert [key["type"] for key in body["keys"]] == [
        "md5_quick",
        "video_stream",
        "tmdb",
        "title_year",
    ]
    assert body["keys"][2] == {"type": "tmdb", "kind": "movie", "id": "603"}
    assert body["label"] == {"aliasId": "extended"} and body["limit"] == 5
    assert match_body(keys(MD5), {"text": "IMAX"})["label"] == {"text": "IMAX"}
    with pytest.raises(CommunityFailure, match="match_keys_unavailable"):
        match_body(FindKeys((), None, None, None), None)


@pytest.mark.parametrize(
    ("tier", "required"),
    [("1", False), ("2", False), ("3", False), ("4", True), ("5", True)]
    + [("runtime_mismatch", True), ("other_version", True)],
)
def test_alignment_required_by_tier(tier, required):
    assert alignment_required(tier) is required


def test_cross_check_accepts_consistent_results_and_order():
    _body, local = query(keys(MD5))
    fuzzy = result(
        versionId="v_2",
        tier="4",
        matchPercent=30,
        matchedKeys=["tmdb"],
        runtimeAgreement="same_cut",
        runtimeMs=8_160_000,
    )
    cross_check(local, [result(), fuzzy])
    with pytest.raises(CommunityFailure, match="invalid_response"):
        cross_check(local, [fuzzy, result()])


@pytest.mark.parametrize(
    "changes",
    [
        {"matchPercent": 100, "tier": "1", "matchedKeys": ["md5_quick"]},
        {"matchedKeys": ["sha256_full"]},
        {
            "tier": "4",
            "matchPercent": 30,
            "matchedKeys": ["md5_quick"],
            "runtimeAgreement": "same_cut",
        },
        {
            "tier": "4",
            "matchPercent": 30,
            "matchedKeys": ["tmdb"],
            "runtimeAgreement": "same_cut",
            "runtimeMs": 8_000_000,
        },
        {
            "tier": "4",
            "matchPercent": 30,
            "matchedKeys": ["tmdb"],
            "runtimeAgreement": "same_cut",
            "runtimeMs": 8_160_000,
            "aliasId": "extended",
        },
    ],
)
def test_cross_check_refuses_inconsistent_scores(changes):
    _body, local = query(keys(MD5))
    with pytest.raises(CommunityFailure, match="invalid_response"):
        cross_check(local, [result(**changes)])


def test_find_versions_queries_the_service_and_reports_tiers(tmp_path):
    service = FakeService()
    _factory, client = connect(tmp_path, service)
    service.on("POST", "/v1/match", lambda _r: {"version": 1, "results": [MATCH]})
    (found,) = asyncio.run(find_versions(MasterService(client), keys(MD5)))
    output = found.as_json("link-1")
    assert output["alignmentRequired"] is False and output["linkedLinkId"] == "link-1"
    assert output["unconfirmed"] is False and output["matchPercent"] == 80
    service.on("POST", "/v1/match", lambda _r: {"version": 1, "results": [{**MATCH, "tier": "1"}]})
    with pytest.raises(CommunityFailure, match="invalid_response"):
        asyncio.run(find_versions(MasterService(client), keys(MD5)))
    client.db.close()


def test_work_identity_for_movies_episodes_and_missing_ids():
    assert work_identity(MOVIE, title="The Matrix", year=1999).as_artifact()["tmdb"] == "603"
    episode = MediaIdentity(
        "episode",
        (ProviderIdentifier("tvdb", "episode", "999", "jellyfin.item"),),
        (ProviderIdentifier("tvdb", "series", "121361", "jellyfin.series"),),
        (EpisodeNumber("aired", 1, 2),),
        (EpisodeNumber("aired", 1, 2), EpisodeNumber("aired", 1, 3)),
    )
    work = work_identity(episode, title="Pilot", year=2011)
    assert (work.kind, work.tvdb, work.season, work.episode, work.last_episode) == (
        "episode",
        "121361",
        1,
        2,
        3,
    )
    assert work.provider_keys()[0].id == "121361"
    with pytest.raises(WorkIdentityRequired) as raised:
        work_identity(MediaIdentity("movie", ()), title="Untitled", year=None)
    assert raised.value.code == "work_identity_required"
    series_only = MediaIdentity("episode", (), (), (EpisodeNumber("aired", 1, 2),))
    with pytest.raises(WorkIdentityRequired):
        work_identity(series_only, title="Pilot", year=None)


def test_find_keys_bind_downloads_to_the_stored_match(tmp_path):
    from pathlib import Path

    from scanner.integrations.community.find_store import FindStore
    from scanner.integrations.community.find_targets import CapturedFind
    from scanner.integrations.community.masters import MasterSets
    from tests.community_master_service_support import master_response, master_route
    from tests.community_master_support import MEMBER, OWNER, VERSION_ID

    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    store = FindStore(clock=clock)
    service = FakeService()
    _factory, client = connect(tmp_path, service)
    service.on("POST", "/v1/match", lambda _r: {"version": 1, "results": [MATCH]})
    master_route(service, "v_12ab", {None: master_response("v_12ab", 1)})
    masters = MasterSets(client, OWNER, store=store)
    captured = CapturedFind(Path("/m.mkv"), (), keys(MD5), None, "generation-1", ())
    found = asyncio.run(masters.find(captured, VERSION_ID))
    assert found["keysUsed"] == ["md5_quick", "tmdb", "title_year"]
    assert found["results"][0]["linkedLinkId"] is None

    def download(key, version="v_12ab", **options):
        return asyncio.run(masters.download(find_key=key, community_version_id=version, **options))

    for key, version, options, code in (
        ("unknown", "v_12ab", {}, "find_expired"),
        (found["findKey"], "v_other", {}, "find_expired"),
        (found["findKey"], "v_12ab", {"guard": "generation-2"}, "target_changed"),
    ):
        with pytest.raises(CommunityFailure, match=code):
            download(key, version, **options)
    with pytest.raises(CommunityFailure, match="find_expired"):
        asyncio.run(
            MasterSets(client, MEMBER, store=store).download(
                find_key=found["findKey"], community_version_id="v_12ab"
            )
        )
    link = download(found["findKey"], guard="generation-1")
    assert (link.tier, link.match_percent, link.alignment_state) == ("2", 80, "not_required")
    clock.now += 1801
    with pytest.raises(CommunityFailure, match="find_expired"):
        download(found["findKey"])
    client.db.close()
