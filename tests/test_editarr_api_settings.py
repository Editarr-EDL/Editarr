
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from scanner import api, repository
from scanner.integrations.jellyfin import JellyfinClient, JellyfinError
from scanner.integrations.jellyfin_server_url import normalize_server_url
from scanner.models import (
    EditAction,
    FilterRuleDisposition,
    JellyfinLibrary,
    JellyfinMediaItem,
    JellyfinSettingsUpdate,
    LibraryReviewCreate,
    McfCategory,
    McfCategoryFamily,
    McfChannel,
    ReviewFilterInput,
    Severity,
)
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import rotate_api_key
from scanner.security.auth_repository import owner_id
from tests.credits_api_support import review_caller
from tests.editarr_support import _isolate_api_runtime
from tests.editarr_support import db as db
from tests.multi_user_access_support import add_linked_owner


def test_jellyfin_client_uses_server_side_token_and_normalizes_payloads():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/jellyfin/Library/VirtualFolders":
            return httpx.Response(
                200,
                json=[
                    {
                        "ItemId": "movies",
                        "Name": "Movies",
                        "CollectionType": "movies",
                        "Locations": ["/remote/media/movies"],
                    }
                ],
            )
        if request.url.path == "/jellyfin/Items":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "Id": "movie-1",
                            "Type": "Movie",
                            "Name": "Example",
                            "Path": "/remote/media/movies/example.mkv",
                            "ImageTags": {"Primary": "poster-tag"},
                            "Container": "mkv",
                            "ProviderIds": {"Tmdb": "123"},
                            "RunTimeTicks": 57_000_000_000,
                            "MediaSourceCount": 3,
                            "MediaStreams": [
                                {"Type": "Video", "Codec": "h264", "Height": 2160}
                            ],
                            "MediaSources": [
                                {
                                    "Id": "source-1",
                                    "Name": "4K HDR",
                                    "SceneName": "Example.2025.2160p.WEB-DL-GROUP",
                                    "ReleaseGroup": "GROUP",
                                    "Edition": "Director's Cut",
                                    "Container": "mkv",
                                    "RunTimeTicks": 57_000_000_000,
                                    "Size": 10_000_000_000,
                                    "MediaStreams": [
                                        {"Type": "Video", "Codec": "hevc", "Height": 2160},
                                        {"Type": "Audio", "Codec": "aac"},
                                        {"Type": "Audio", "Codec": "ac3", "IsExternal": False},
                                        {"Type": "Audio", "Codec": "ac3", "IsExternal": True},
                                        {"Type": "Subtitle", "Codec": "srt"},
                                    ],
                                },
                                {
                                    "Id": "source-2",
                                    "MediaStreams": [{"Type": "Video"}],
                                },
                                {"Id": "source-3"},
                            ],
                            "OfficialRating": "PG-13",
                        }
                    ]
                },
            )
        if request.url.path == "/jellyfin/Shows/series-1/Seasons":
            return httpx.Response(
                200,
                json={
                    "Items": [
                        {
                            "Id": "season-1",
                            "Type": "Season",
                            "Name": "Season 1",
                            "IndexNumber": 1,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    with JellyfinClient("http://jellyfin.test/jellyfin/", "api-key", http_client) as client:
        assert client.libraries()[0].name == "Movies"
        item = client.items("movies")[0]
        detail = client.item("movie-1")
        seasons = client.seasons("series-1")

    assert item.id == "movie-1"
    assert item.path == "/remote/media/movies/example.mkv"
    assert item.media_streams == [{"Type": "Video", "Codec": "h264", "Height": 2160}]
    assert item.official_rating == "PG-13"
    assert item.resolution == "4K"
    assert item.media_format == "MKV · H264"
    assert item.media_provider_id == "tmdb:123"
    assert item.duration_seconds == 5700
    assert detail.id == "movie-1"
    assert detail.library_id == "movies"
    assert detail.media_source_count == 3
    assert detail.versions[0].name == "4K HDR"
    assert detail.versions[0].scene_name == "Example.2025.2160p.WEB-DL-GROUP"
    assert detail.versions[0].release_group == "GROUP"
    assert detail.versions[0].edition == "Director's Cut"
    assert detail.versions[0].media_format == "MKV · HEVC"
    assert detail.versions[0].size_bytes == 10_000_000_000
    assert [version.audio_track_count for version in detail.versions] == [2, 0, None]
    assert seasons[0].index_number == 1
    episode = JellyfinClient._media_item(
        {
            "Id": "episode-1",
            "Type": "Episode",
            "Name": "Episode",
            "ParentIndexNumber": 2,
            "IndexNumber": 4,
        },
        "shows",
    )
    assert episode.season_number == 2
    assert episode.index_number == 4
    assert all(
        request.headers["Authorization"] == 'MediaBrowser Token="api-key"'
        for request in seen
    )
    assert seen[1].url.params["ParentId"] == "movies"
    assert seen[2].url.params["Ids"] == "movie-1"
    assert seen[3].url.path == "/jellyfin/Library/VirtualFolders"
    assert seen[4].url.path == "/jellyfin/Shows/series-1/Seasons"
    assert normalize_server_url("https://example.test/base/") == "https://example.test/base"
    with pytest.raises(JellyfinError, match="must not include credentials"):
        normalize_server_url("https://user:password@example.test")


def test_jellyfin_episode_review_persists_catalog_season(
    db, monkeypatch, tmp_path
):
    from scanner.http import catalog_targets

    video = tmp_path / "episode.mkv"
    video.touch()
    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(
            server_url="http://jellyfin.test", api_key="test-key"
        ),
    )

    class FakeJellyfinClient:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def libraries(self):
            return [JellyfinLibrary(id="a" * 32, name="Shows", locations=[str(tmp_path)])]

        def item(self, _item_id):
            return JellyfinMediaItem(
                id="e" * 32,
                library_id="a" * 32,
                item_type="Episode",
                name="Episode",
                year=2026,
                season_number=2,
                index_number=4,
                path=str(video),
                duration_seconds=1800,
            )

    monkeypatch.setattr(api, "JellyfinClient", FakeJellyfinClient)
    catalog_targets.jellyfin_libraries.clear()
    try:
        review = api.api_create_jellyfin_review(
            "e" * 32, LibraryReviewCreate(name="Episode review"), review_caller(db)
        )
    finally:
        catalog_targets.jellyfin_libraries.clear()

    assert review.mcf_document.season == 2
    assert review.mcf_document.episode == 4


@pytest.mark.parametrize("base_path", ["", "/jellyfin"])
def test_jellyfin_client_works_when_legacy_authorization_is_disabled(base_path):
    """Model Jellyfin 12 rejecting legacy auth for catalog and image requests."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("Authorization") != 'MediaBrowser Token="api-key"':
            return httpx.Response(401)
        assert "X-Emby-Token" not in request.headers
        assert "X-MediaBrowser-Token" not in request.headers
        assert "X-Emby-Authorization" not in request.headers
        assert "api_key" not in request.url.params
        assert "ApiKey" not in request.url.params
        path = request.url.path.removeprefix(base_path) if base_path else request.url.path
        if path == "/Library/VirtualFolders":
            assert request.headers["Accept"] == "application/json"
            return httpx.Response(200, json=[{"ItemId": "library", "Name": "Movies"}])
        if path in {"/Items", "/Shows/series/Seasons"}:
            fields = request.url.params["Fields"].split(",")
            assert "OfficialRating" not in fields
            assert "IndexNumber" not in fields
            item = {"Id": "item", "Name": "Example", "Type": "Movie"}
            # Jellyfin only attaches hierarchy metadata when requested.
            if "ParentId" in fields:
                item["ParentId"] = "parent"
            return httpx.Response(
                200, json={"Items": [item]}
            )
        if path == "/Items/item/Images/Primary":
            assert request.headers["Accept"] == "image/*"
            assert request.url.params["tag"] == "poster-tag"
            return httpx.Response(200, content=b"poster", headers={"Content-Type": "image/png"})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with JellyfinClient(
            f"http://jellyfin.test{base_path}/", " api-key ", http_client
        ) as client:
            client.test_connection()
            assert client.libraries()[0].id == "library"
            assert client.items("library")[0].parent_id == "parent"
            assert client.children("season")[0].parent_id == "parent"
            assert client.item("item").parent_id == "parent"
            assert client.seasons("series")[0].parent_id == "parent"
            assert client.primary_image("item", "poster-tag") == (b"poster", "image/png")
    assert len(seen) == 7


def test_jellyfin_authorization_encodes_token_as_one_header_parameter():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        with JellyfinClient(
            "http://jellyfin.test", 'bad", Token="key\nvalue', http_client
        ) as client:
            with pytest.raises(JellyfinError, match="rejected the configured API key"):
                client.test_connection()
    assert seen[0].headers["Authorization"] == (
        'MediaBrowser Token="bad%22%2C%20Token%3D%22key%0Avalue"'
    )


def test_jellyfin_client_returns_safe_authentication_errors():
    client = JellyfinClient(
        "http://jellyfin.test",
        "secret",
        httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(401))),
    )
    with pytest.raises(JellyfinError, match="rejected the configured API key"):
        client.libraries()


def test_settings_api_tests_before_saving_and_never_returns_key(monkeypatch, db, tmp_path):
    def override_db():
        yield db

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(api, "init_db", lambda: None)
    _isolate_api_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    monkeypatch.setattr(api, "recover_cancelled_scan_queue_items", lambda _db: 0)
    monkeypatch.setattr(api, "scanning_scan_worker_processes", lambda _db: [])
    monkeypatch.setattr(api, "mark_orphaned_scan_workers", lambda _db, _pids: [])
    monkeypatch.setattr(api, "get_session_factory", lambda: sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(
        "scanner.security.auth_policy.get_session_factory", lambda: sessionmaker(bind=db.get_bind())
    )
    add_linked_owner(db, "Admin")
    api_key = rotate_api_key(db, owner_id(db), load_api_key_wrap_key(tmp_path))
    monkeypatch.setattr(api, "_test_jellyfin_connection", lambda url, key: calls.append((url, key)))
    api.app.dependency_overrides[api.get_db] = override_db
    try:
        with TestClient(api.app, headers={"X-Api-Key": api_key}) as client:
            response = client.put(
                "/api/settings/jellyfin",
                json={"server_url": "http://jellyfin.test/", "api_key": "not-for-clients"},
            )
            assert response.status_code == 200
            assert response.json() == {
                "server_url": "http://jellyfin.test",
                "api_key_configured": True,
                "last_checked_at": None,
                "last_error": None,
            }
            assert "not-for-clients" not in response.text
            assert client.get("/api/settings/jellyfin").json()["api_key_configured"] is True
    finally:
        api.app.dependency_overrides.clear()

    assert calls == [("http://jellyfin.test", "not-for-clients")]


def test_default_filter_settings_api_updates_and_reads(monkeypatch, db, tmp_path):
    def override_db():
        yield db

    monkeypatch.setattr(api, "init_db", lambda: None)
    _isolate_api_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    monkeypatch.setattr(api, "recover_cancelled_scan_queue_items", lambda _db: 0)
    monkeypatch.setattr(api, "scanning_scan_worker_processes", lambda _db: [])
    monkeypatch.setattr(api, "mark_orphaned_scan_workers", lambda _db, _pids: [])
    monkeypatch.setattr(api, "get_session_factory", lambda: sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(
        "scanner.security.auth_policy.get_session_factory", lambda: sessionmaker(bind=db.get_bind())
    )
    add_linked_owner(db, "Admin")
    api_key = rotate_api_key(db, owner_id(db), load_api_key_wrap_key(tmp_path))
    api.app.dependency_overrides[api.get_db] = override_db
    try:
        with TestClient(api.app, headers={"X-Api-Key": api_key}) as client:
            settings = client.get("/api/settings/filters").json()
            assert settings["revision"] == 0
            assert settings["selection_rules"] == []
            assert len(settings["severity_defaults"]) == 97
            assert settings["severity_defaults"][0] == {
                "subcategory": "commercial",
                "severity": "medium",
            }

            response = client.put(
                "/api/settings/filters",
                json={
                    **settings,
                    "selection_rules": [
                        {
                            "id": "7b8672d7-c7be-4bd7-a596-b9a0b55ac425",
                            "disposition": FilterRuleDisposition.ALLOW,
                            "evidence_source": "audio",
                            "category": "language",
                            "subcategory": "swearing",
                            "label_contains": "  bad word  ",
                            "severity": None,
                            "mcf_channel": None,
                        }
                    ],
                },
            )
            assert response.status_code == 200
            updated = response.json()
            assert updated["revision"] == 1
            assert updated["selection_rules"][0]["label_contains"] == "bad word"

            stale = client.put("/api/settings/filters", json=settings)
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "stale_settings_revision"

            draft_filter = ReviewFilterInput(
                id="27bb1561-92e4-41d5-a492-f0f9bbfbcc95",
                source_scan_id="scan-1",
                source_finding_id=1,
                source="audio",
                category=McfCategoryFamily.LANGUAGE,
                subcategory=McfCategory.swearing,
                severity=Severity.HIGH,
                mcf_channel=McfChannel.VIDEO,
                mcf_comment=None,
                label="Bad-word!",
                details=None,
                group_key=None,
                start_seconds=1,
                end_seconds=2,
                confidence=0.01,
                edit_action=EditAction.MUTE,
                selected=True,
            )
            applied = client.post(
                "/api/settings/filters/apply",
                json={"filters": [draft_filter.model_dump(mode="json")]},
            )
            assert applied.status_code == 200
            assert applied.json()["filters"][0]["severity"] == "medium"
            assert applied.json()["filters"][0]["mcf_channel"] == "audio"
            assert applied.json()["filters"][0]["selected"] is False
            assert applied.json()["selection_changes"] == 1
            assert applied.json()["severity_changes"] == 1
            assert applied.json()["channel_changes"] == 1
    finally:
        api.app.dependency_overrides.clear()


def test_diagnostic_log_api_saves_debug_preference_and_redacts_log_tails(monkeypatch, db, tmp_path):
    def override_db():
        yield db

    monkeypatch.setattr(api, "init_db", lambda: None)
    _isolate_api_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_start_scan_queue_worker_if_needed", lambda: None)
    monkeypatch.setattr(api, "recover_cancelled_scan_queue_items", lambda _db: 0)
    monkeypatch.setattr(api, "scanning_scan_worker_processes", lambda _db: [])
    monkeypatch.setattr(api, "mark_orphaned_scan_workers", lambda _db, _pids: [])
    monkeypatch.setattr(api, "get_session_factory", lambda: sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(
        "scanner.security.auth_policy.get_session_factory", lambda: sessionmaker(bind=db.get_bind())
    )
    add_linked_owner(db, "Admin")
    api_key = rotate_api_key(db, owner_id(db), load_api_key_wrap_key(tmp_path))
    api.app.dependency_overrides[api.get_db] = override_db
    try:
        with TestClient(api.app, headers={"X-Api-Key": api_key}) as client:
            assert client.get("/api/system/logs/settings").json() == {"debug_enabled": False}
            response = client.put("/api/system/logs/settings", json={"debug_enabled": True})
            assert response.status_code == 200
            assert response.json() == {"debug_enabled": True}
            response = client.put("/api/system/logs/settings", json={"debug_enabled": False})
            assert response.status_code == 200
            assert response.json() == {"debug_enabled": False}

            (tmp_path / "logs" / "worker.log").write_text(
                "scan accepted\ntoken=not-for-users\n", encoding="utf-8"
            )
            response = client.get("/api/system/logs/worker?limit=10")
            assert response.status_code == 200
            assert response.json() == {
                "channel": "worker",
                "lines": ["scan accepted", "token=[REDACTED]"],
                "truncated": False,
            }
            assert client.get("/api/system/logs/auth-audit").status_code == 400
    finally:
        api.app.dependency_overrides.clear()


