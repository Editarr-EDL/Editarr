from pathlib import Path

import pytest
from fastapi import HTTPException

from scanner import api, repository
from scanner.db import (
    ScanRow,
)
from scanner.http.catalog_library_api import _edl_filter_matches
from scanner.models import (
    ExportFormat,
    ExportSummary,
    JellyfinMediaItem,
    JellyfinMediaVersion,
    JellyfinSettingsUpdate,
)
from scanner.pipeline.cache import compute_video_md5
from tests.catalog_access_support import owner_viewer
from tests.editarr_support import db as db
from tests.editarr_support import record_edl_export
from tests.scan_access_support import owner_caller


def test_library_apis_use_the_saved_server_side_credentials(monkeypatch, db):
    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key="saved-server-key"),
    )
    calls: list[tuple[object, ...]] = []

    class FakeJellyfinClient:
        def __init__(self, server_url, api_key):
            calls.append((server_url, api_key))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def libraries(self):
            return [
                {
                    "id": "movies",
                    "name": "Movies",
                    "collection_type": "movies",
                    "locations": ["/remote/movies"],
                }
            ]

        def all_items(self, library_id, *, recursive=True, include_item_types=(), search_term=""):
            calls.append((library_id, recursive, include_item_types, search_term))
            if search_term:
                # Jellyfin may not match a spelling with different accents than the title.
                return []
            return [
                JellyfinMediaItem(
                    id="movie-1",
                    library_id=library_id,
                    parent_id=None,
                    item_type="Movie",
                    name="Zèbra",
                    sort_name=None,
                    year=2026,
                    overview=None,
                    path="/remote/zebra.mkv",
                    runtime_ticks=None,
                    duration_seconds=7200,
                    resolution="1080p",
                    media_format="MKV · H264",
                ),
                JellyfinMediaItem(
                    id="movie-2",
                    library_id=library_id,
                    parent_id=None,
                    item_type="Movie",
                    name="Álpha",
                    sort_name=None,
                    year=2024,
                    overview=None,
                    path="/remote/alpha.mkv",
                    runtime_ticks=None,
                    duration_seconds=5400,
                    resolution="4K",
                    media_format="MKV · HEVC",
                ),
                JellyfinMediaItem(
                    id="movie-3",
                    library_id=library_id,
                    parent_id=None,
                    item_type="Movie",
                    name="Beta",
                    sort_name=None,
                    year=None,
                    overview=None,
                    path="/remote/beta.mkv",
                    runtime_ticks=None,
                    duration_seconds=1800,
                    resolution="720p",
                    media_format="MP4 · H264",
                ),
                JellyfinMediaItem(
                    id="folder-1",
                    library_id=library_id,
                    parent_id=None,
                    item_type="Folder",
                    name="Extras",
                    sort_name=None,
                    year=None,
                    overview=None,
                    path="/remote/extras",
                    runtime_ticks=None,
                ),
            ]

    monkeypatch.setattr(api, "JellyfinClient", FakeJellyfinClient)

    assert api.api_list_jellyfin_libraries(db, viewer=owner_viewer(db)) == [
        {
            "id": "movies",
            "name": "Movies",
            "collection_type": "movies",
            "locations": ["/remote/movies"],
        }
    ]
    exported_media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-2",
        library_id="movies",
        item_type="Movie",
        name="Álpha",
        path="/remote/alpha.mkv",
    )
    exported_scan = repository.create_scan(
        db,
        media_item=exported_media_item,
        resolved_local_path="/scanner/alpha.mkv",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        frame_interval=None,
        speed_up=False,
    )
    record_edl_export(
        db,
        media_item_id=exported_media_item.id,
        media_source_id="",
        scan_id=exported_scan.id,
    )

    page = api.api_list_jellyfin_library_items(
        "movies", start_index=1, limit=1, sort_by="title", db=db, viewer=owner_viewer(db)
    )
    assert page.total_count == 3
    assert [item.name for item in page.items] == ["Beta"]
    assert page.items[0].image_url == "/api/library-items/movie-3/image?principal=owner-test"
    assert page.resolution_values == ["4K", "1080p", "720p"]
    assert page.title_letter_offsets == {"A": 0, "B": 1, "Z": 2}

    exported = api.api_list_jellyfin_library_items(
        "movies", edl_filter="exported", search="alpha", db=db, viewer=owner_viewer(db)
    )
    assert exported.total_count == 1
    exported_edl = exported.items[0].exports.exports[ExportFormat.EDL]
    assert exported_edl.present is True
    assert [(ref.id, ref.scan_id, ref.role) for ref in exported_edl.exported_scans] == [
        (exported_scan.id, 1, "audio")
    ]

    four_k = api.api_list_jellyfin_library_items(
        "movies", resolution_filter="4K", sort_by="duration", db=db, viewer=owner_viewer(db)
    )
    assert [item.id for item in four_k.items] == ["movie-2"]

    not_exported = api.api_list_jellyfin_library_items(
        "movies", edl_filter="not_exported", sort_by="year", db=db, viewer=owner_viewer(db)
    )
    assert [item.id for item in not_exported.items] == ["movie-1", "movie-3"]
    assert not_exported.title_letter_offsets == {}
    newest = api.api_list_jellyfin_library_items(
        "movies", sort_direction="desc", db=db, viewer=owner_viewer(db)
    )
    assert newest.title_letter_offsets == {"Z": 0, "B": 1, "A": 2}
    refreshed = api.api_list_jellyfin_library_items(
        "movies", refresh=True, db=db, viewer=owner_viewer(db)
    )
    assert refreshed.total_count == 3
    assert calls == [
        ("http://jellyfin.test", "saved-server-key"),
        ("http://jellyfin.test", "saved-server-key"),
        ("movies", False, ("Movie", "Series", "Video", "MusicVideo"), ""),
        ("http://jellyfin.test", "saved-server-key"),
        ("movies", False, ("Movie", "Series", "Video", "MusicVideo"), "alpha"),
        ("http://jellyfin.test", "saved-server-key"),
        ("movies", False, ("Movie", "Series", "Video", "MusicVideo"), ""),
    ]


def test_library_item_scan_history_is_available_without_jellyfin_settings(db):
    media_item = repository.upsert_media_item(
        db,
        server_id="http://previous-jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path="/remote/example.mkv",
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path="/scanner/example.mkv",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        frame_interval=None,
        speed_up=False,
    )

    assert [
        item.id for item in api.api_list_library_item_scans("movie-1", db, caller=owner_caller(db))
    ] == [scan.id]


def test_item_scan_history_stays_with_its_item_for_a_shared_file(db, tmp_path: Path):
    shared_path = str((tmp_path / "shared.mkv").resolve())
    jellyfin_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path=shared_path,
    )
    other_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-3",
        library_id="collection",
        item_type="Movie",
        name="Example (collection copy)",
        path=shared_path,
    )
    unrelated_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-2",
        library_id="movies",
        item_type="Movie",
        name="Different",
        path=str((tmp_path / "different.mkv").resolve()),
    )
    scan_options = {
        "scan_audio": True,
        "audio_model": "whisper",
        "audio_filter": "better_profanity",
        "scan_video": False,
        "video_model": None,
        "frame_interval": None,
        "speed_up": False,
    }
    jellyfin_scan = repository.create_scan(
        db, media_item=jellyfin_item, resolved_local_path=shared_path, **scan_options
    )
    other_scan = repository.create_scan(
        db, media_item=other_item, resolved_local_path=shared_path, **scan_options
    )
    repository.create_scan(
        db,
        media_item=unrelated_item,
        resolved_local_path=str((tmp_path / "different.mkv").resolve()),
        **scan_options,
    )

    scans = api.api_list_library_item_scans("movie-1", db, caller=owner_caller(db))
    other_scans = api.api_list_library_item_scans("movie-3", db, caller=owner_caller(db))

    assert [scan.id for scan in scans] == [jellyfin_scan.id]
    assert [scan.id for scan in other_scans] == [other_scan.id]


def test_filter_candidate_search_uses_metadata_and_the_video_cache_hash(
    monkeypatch, db, tmp_path: Path
):
    target_path = tmp_path / "target.mkv"
    hash_match_path = tmp_path / "hash-match.mkv"
    metadata_match_path = tmp_path / "metadata-match.mkv"
    file_name_match_path = tmp_path / "other-library" / target_path.name
    format_match_path = tmp_path / "format-match.mkv"
    unrelated_path = tmp_path / "unrelated.mkv"
    target_path.write_bytes(b"same-video-content")
    hash_match_path.write_bytes(b"same-video-content")
    metadata_match_path.write_bytes(b"different-content")
    file_name_match_path.parent.mkdir()
    file_name_match_path.write_bytes(b"different-content")
    format_match_path.write_bytes(b"different-content")
    unrelated_path.write_bytes(b"unrelated-content")
    scan_options = {
        "scan_audio": True,
        "audio_model": "whisper",
        "audio_filter": "better_profanity",
        "scan_video": False,
        "video_model": None,
        "frame_interval": None,
        "speed_up": False,
    }

    def create_completed_scan(
        item_id: str,
        name: str,
        path: Path,
        duration_seconds: float,
        *,
        scene_name: str | None = None,
        release_group: str | None = None,
        media_format: str | None = None,
        media_provider_id: str = "tmdb:123",
    ):
        media_item = repository.upsert_media_item(
            db,
            server_id="http://jellyfin.test",
            jellyfin_id=item_id,
            library_id="movies",
            item_type="Movie",
            name=name,
            path=str(path),
        )
        scan = repository.create_scan(
            db,
            media_item=media_item,
            resolved_local_path=str(path),
            duration_seconds=duration_seconds,
            scene_name=scene_name,
            release_group=release_group,
            media_format=media_format,
            media_provider_id=media_provider_id,
            **scan_options,
        )
        repository.update_scan_status(db, scan.id, "complete")
        return scan

    hash_match = create_completed_scan(
        "movie-hash", "Different Title", hash_match_path, 120, media_format="MP4"
    )
    metadata_match = create_completed_scan(
        "movie-metadata",
        "Different Title",
        metadata_match_path,
        99,
        scene_name="Example.Movie.2025.2160p.WEB-DL-GROUP",
        release_group="GROUP",
    )
    file_name_match = create_completed_scan(
        "movie-file-name", "Different Title", file_name_match_path, 120
    )
    format_match = create_completed_scan("movie-format", "Different Title", format_match_path, 120)
    wrong_provider_match = create_completed_scan(
        "movie-wrong-provider",
        "Different Title",
        tmp_path / "wrong-provider.mkv",
        120,
        scene_name="Example.Movie.2025.2160p.WEB-DL-GROUP",
        release_group="GROUP",
        media_provider_id="tvdb:456",
    )
    create_completed_scan("movie-unrelated", "Different Title", unrelated_path, 99)

    target = JellyfinMediaItem(
        id="movie-target",
        library_id="movies",
        item_type="Movie",
        name="Example Movie",
        path=str(target_path),
        duration_seconds=120,
        media_format="MKV",
        media_provider_id="tmdb:123",
        versions=[
            JellyfinMediaVersion(
                id="target-version",
                path=str(target_path),
                scene_name="Example.Movie.2025.2160p.WEB-DL-GROUP",
                release_group="GROUP",
                media_format="MKV",
            )
        ],
    )

    class FakeJellyfinClient:
        def __init__(self, *_credentials):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def item(self, item_id):
            assert item_id == "movie-target"
            return target

    monkeypatch.setattr(api, "JellyfinClient", FakeJellyfinClient)
    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key="test-key"),
    )

    result = api.api_find_filter_candidates(
        api.ScanMatchSearch(
            source="jellyfin",
            item_id="movie-target",
            library_id="movies",
            source_path=str(target_path),
            item_name="Example Movie",
            item_type="Movie",
            duration_seconds=120,
            scene_name="Example.Movie.2025.2160p.WEB-DL-GROUP",
            release_group="GROUP",
            media_format="MKV",
            media_provider_id="tmdb:123",
        ),
        db,
        viewer=owner_viewer(db),
    )

    assert db.get(ScanRow, hash_match.id).video_hash == compute_video_md5(str(hash_match_path))
    assert {(match.scan.id, match.match_reason) for match in result.matches} == {
        (hash_match.id, "quick_hash"),
        (metadata_match.id, "scene_release"),
        (file_name_match.id, "file_name"),
        (format_match.id, "format"),
    }
    assert wrong_provider_match.id not in {match.scan.id for match in result.matches}
    assert not any(match.already_associated for match in result.matches)

    api.api_associate_filter_candidates(
        api.ScanAssociationUpdate(media_item_id=result.media_item_id, scan_ids=[hash_match.id]),
        db,
        viewer=owner_viewer(db),
    )
    associated_result = api.api_find_filter_candidates(
        api.ScanMatchSearch(
            source="jellyfin",
            item_id="movie-target",
            library_id="movies",
            source_path=str(target_path),
            item_name="Example Movie",
            item_type="Movie",
            duration_seconds=120,
            scene_name="Example.Movie.2025.2160p.WEB-DL-GROUP",
            release_group="GROUP",
            media_format="MKV",
            media_provider_id="tmdb:123",
        ),
        db,
        viewer=owner_viewer(db),
    )
    assert {match.scan.id for match in associated_result.matches if match.already_associated} == {
        hash_match.id
    }
    assert {
        scan.id
        for scan in repository.list_scans_for_library_item(
            db, jellyfin_item_id="movie-target", server_id="http://jellyfin.test"
        )
    } == {hash_match.id}


def test_library_browse_items_prefers_series_and_excludes_containers():
    series = JellyfinMediaItem(
        id="series-1", library_id="shows", item_type="Series", name="Example Show"
    )
    episode = JellyfinMediaItem(
        id="episode-1",
        library_id="shows",
        parent_id="season-1",
        item_type="Episode",
        name="Episode 1",
        path="/remote/shows/example.mkv",
    )
    folder = JellyfinMediaItem(
        id="folder-1", library_id="shows", item_type="Folder", name="Extras", path="/remote/extras"
    )

    assert api._library_browse_items("shows", [folder, episode, series]) == [series]


def test_media_version_edl_statuses_are_scoped_to_the_exported_source(db):
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path="/remote/example.mkv",
    )
    exported_scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path="/scanner/example-4k.mkv",
        media_source_id="source-4k",
        media_source_name="4K HDR",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        frame_interval=None,
        speed_up=False,
    )
    record_edl_export(
        db,
        media_item_id=media_item.id,
        media_source_id="source-4k",
        scan_id=exported_scan.id,
    )

    item = JellyfinMediaItem(
        id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        versions=[
            JellyfinMediaVersion(id="source-4k", name="4K HDR"),
            JellyfinMediaVersion(id="source-1080p", name="1080p SDR"),
        ],
    )
    decorated = api._with_library_item_state(db, server_id="http://jellyfin.test", items=[item])[0]

    decorated_edl = decorated.exports.exports[ExportFormat.EDL]
    assert decorated_edl.present is True
    assert [(ref.id, ref.scan_id, ref.role) for ref in decorated_edl.exported_scans] == [
        (exported_scan.id, 1, "audio")
    ]
    version_statuses = [
        (
            version.exports.exports[ExportFormat.EDL].present,
            [
                (ref.id, ref.scan_id, ref.role)
                for ref in version.exports.exports[ExportFormat.EDL].exported_scans
            ],
        )
        for version in decorated.versions
    ]
    assert version_statuses == [
        (True, [(exported_scan.id, 1, "audio")]),
        (False, []),
    ]


def test_primary_image_api_proxies_artwork_without_exposing_credentials(monkeypatch, db):
    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key="saved-server-key"),
    )
    calls: list[tuple[str, str | None]] = []

    class FakeJellyfinClient:
        def __init__(self, *_credentials):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def primary_image(self, item_id, tag):
            calls.append((item_id, tag))
            return b"poster-bytes", "image/png"

    monkeypatch.setattr(api, "JellyfinClient", FakeJellyfinClient)

    response = api.api_library_item_primary_image(
        "movie-1", tag="poster-tag", db=db, viewer=owner_viewer(db)
    )

    assert response.body == b"poster-bytes"
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert calls == [("movie-1", "poster-tag")]


def test_title_order_groups_non_letters_first_with_matching_letter_offsets():
    from scanner.http import catalog_library_api as catalog

    names = [
        "Zèbra",
        "Ωmega",
        "straße",
        "ßeta",
        "2001",
        "Élan",
        "Ägypten",
        "Ярость",
        "!Bang",
        "Beta",
    ]
    items = [
        JellyfinMediaItem(id=f"item-{index}", item_type="Movie", name=name)
        for index, name in enumerate(names)
    ]
    ordered = sorted(items, key=lambda item: catalog._library_sort_value(item, "title"))

    assert [item.name for item in ordered] == [
        "!Bang",
        "2001",
        "Ωmega",
        "Ярость",
        "Ägypten",
        "Beta",
        "Élan",
        "ßeta",
        "straße",
        "Zèbra",
    ]
    assert [catalog._title_letter(item) for item in ordered[:4]] == ["#"] * 4
    assert catalog._title_letter(items[3]) == "S"  # "ßeta" folds to "sseta"
    assert catalog._title_letter_offsets(ordered) == {
        "#": 0,
        "A": 4,
        "B": 5,
        "E": 6,
        "S": 7,
        "Z": 9,
    }


def test_dotless_i_titles_sort_and_group_with_i():
    from scanner.http import catalog_library_api as catalog

    items = [
        JellyfinMediaItem(id=f"item-{index}", item_type="Movie", name=name)
        for index, name in enumerate(["Jade", "ıhlara", "Iris", "Hotel"])
    ]
    ordered = sorted(items, key=lambda item: catalog._library_sort_value(item, "title"))

    assert [item.name for item in ordered] == ["Hotel", "ıhlara", "Iris", "Jade"]
    assert catalog._title_letter_offsets(ordered) == {"H": 0, "I": 1, "J": 3}


def test_edl_filter_matches_outdated_exports_only():
    missing = ExportSummary(format=ExportFormat.EDL)
    current = ExportSummary(format=ExportFormat.EDL, present=True, current=True)
    outdated = ExportSummary(format=ExportFormat.EDL, present=True, outdated=True)
    matches = {
        edl_filter: [_edl_filter_matches(edl, edl_filter) for edl in (missing, current, outdated)]
        for edl_filter in ("all", "exported", "not_exported", "outdated")
    }
    assert matches == {
        "all": [True, True, True],
        "exported": [False, True, True],
        "not_exported": [True, False, False],
        "outdated": [False, False, True],
    }


def test_library_items_reject_unknown_edl_filter(db):
    with pytest.raises(HTTPException) as caught:
        api.api_list_jellyfin_library_items(
            "movies", edl_filter="stale", db=db, viewer=owner_viewer(db)
        )
    assert caught.value.status_code == 400
    assert "outdated" in caught.value.detail
