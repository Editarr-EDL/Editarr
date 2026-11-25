"""MU-20: catalog, artwork, candidate, and track routes decide media before anything leaves.

Real HTTP through the access middleware for the owner (1), an admin (2),
Casey (user 4, linked, Family only, the mature film restricted), and a local
user (5) scoped to the Family library. Fixture `mu20-catalog-1`
over `mu11-media-access-4`; see `tests/catalog_access_http.py`.
"""

from __future__ import annotations

import pytest

from scanner import repository
from scanner.db import ScanRow
from scanner.models import JellyfinMediaVersion
from tests.catalog_access_http import LOCAL, serve_catalog
from tests.media_access_support import (
    EPISODE,
    FAMILY,
    FAMILY_FILM,
    FEATURE,
    MATURE,
    MEDIA,
    MOVIES,
    SERIES,
)
from tests.multi_user_access_support import ADMIN, OWNER, USER, live_accounts

CASEY = USER
FAMILY_ITEMS = f"/api/libraries/{FAMILY}/items"
NOT_FOUND = {"code": "not_found"}


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    app = serve_catalog(monkeypatch, accounts, tmp_path)
    yield app
    app.client.close()
    accounts.close()


def _refused(response) -> bool:
    return response.status_code == 404 and response.json()["code"] == NOT_FOUND["code"]


def _names(page) -> list[str]:
    return [item["name"] for item in page["items"]]


def test_lists_count_facet_and_page_only_reachable_items(catalog):
    owner = catalog.get(OWNER, FAMILY_ITEMS).json()
    casey = catalog.get(CASEY, FAMILY_ITEMS).json()

    assert owner["total_count"] == 3 and owner["rating_values"] == ["G", "PG", "R"]
    assert casey["total_count"] == 2 and casey["rating_values"] == ["G", "PG"]
    assert "Synthetic Mature Film" not in _names(casey)
    assert all(item["path"] is None for item in casey["items"])
    assert all(item["path"] for item in owner["items"])

    newest_first = {"sort_direction": "desc", "start_index": 0, "limit": 1}
    assert _names(catalog.get(OWNER, FAMILY_ITEMS, **newest_first).json()) == [
        "Synthetic Mature Film"
    ]
    assert _names(catalog.get(CASEY, FAMILY_ITEMS, **newest_first).json()) == [
        "Synthetic Late Show"
    ]
    second = catalog.get(CASEY, FAMILY_ITEMS, start_index=1, limit=1).json()
    assert _names(second) == ["Synthetic Late Show"] and second["total_count"] == 2
    filtered = catalog.get(CASEY, FAMILY_ITEMS, rating_filter="R").json()
    assert filtered["total_count"] == 0 and filtered["items"] == []

    local = catalog.get(LOCAL, FAMILY_ITEMS).json()
    assert local["total_count"] == 3 and all(item["path"] is None for item in local["items"])
    admin = catalog.get(ADMIN, FAMILY_ITEMS).json()
    assert admin["total_count"] == 3 and all(item["path"] for item in admin["items"])


def test_search_and_seasons_decide_before_anything_is_counted(catalog):
    mature = {"search": "mature"}
    assert catalog.get(CASEY, FAMILY_ITEMS, **mature).json()["total_count"] == 0
    assert catalog.get(OWNER, FAMILY_ITEMS, **mature).json()["total_count"] == 1
    synthetic = catalog.get(CASEY, FAMILY_ITEMS, search="synthetic").json()
    assert synthetic["total_count"] == 2 and "Synthetic Mature Film" not in _names(synthetic)

    for user, refused in ((CASEY, (MATURE, FEATURE)), (LOCAL, (FEATURE,))):
        for item_id in refused:
            assert _refused(catalog.get(user, f"/api/library-items/{item_id}/seasons"))
    assert catalog.get(CASEY, f"/api/library-items/{SERIES}/seasons").json() == []
    assert ("seasons", MATURE) not in catalog.jellyfin.calls


def test_title_search_finds_missing_browse_items_and_filters_access(catalog, monkeypatch):
    all_items = catalog.jellyfin.all_items

    def listing(library_id, *, recursive, include_item_types, search_term=""):
        rows = all_items(library_id, recursive, include_item_types, search_term)
        if search_term:
            return rows
        return [item for item in rows if item.id not in {FAMILY_FILM, MATURE}]

    monkeypatch.setattr(catalog.jellyfin, "all_items", listing)
    assert _names(catalog.get(OWNER, FAMILY_ITEMS).json()) == ["Synthetic Late Show"]

    owner = catalog.get(OWNER, FAMILY_ITEMS, search="synthetic").json()
    casey = catalog.get(CASEY, FAMILY_ITEMS, search="synthetic").json()
    assert owner["total_count"] == 3 and "Synthetic Family Film" in _names(owner)
    assert casey["total_count"] == 2 and "Synthetic Family Film" in _names(casey)
    assert "Synthetic Mature Film" not in _names(casey)
    assert casey["rating_values"] == ["G", "PG"]
    assert all(item["path"] is None for item in casey["items"])
    assert _names(catalog.get(OWNER, FAMILY_ITEMS).json()) == ["Synthetic Late Show"]


def test_restricted_libraries_are_absent_and_refused(catalog):
    assert [library["id"] for library in catalog.get(OWNER, "/api/libraries").json()] == [
        MOVIES,
        FAMILY,
    ]
    for user in (CASEY, LOCAL):
        libraries = catalog.get(user, "/api/libraries").json()
        assert libraries == [
            {"id": FAMILY, "name": "Family", "collection_type": None, "locations": []}
        ]
        assert _refused(catalog.get(user, f"/api/libraries/{MOVIES}/items"))
    assert catalog.get(OWNER, f"/api/libraries/{MOVIES}/items").json()["total_count"] == 1


def test_direct_reads_and_artwork_refuse_restricted_items_after_the_owner_cached_them(catalog):
    assert catalog.get(OWNER, f"/api/library-items/{MATURE}").json()["path"]
    assert catalog.get(OWNER, f"/api/library-items/{MATURE}/image").status_code == 200

    for user, refused in ((CASEY, (MATURE, FEATURE)), (LOCAL, (FEATURE,))):
        for item_id in refused:
            assert _refused(catalog.get(user, f"/api/library-items/{item_id}"))
            assert _refused(catalog.get(user, f"/api/library-items/{item_id}/image"))
            assert _refused(catalog.get(user, f"/api/library-items/{item_id}/children"))
    assert catalog.jellyfin.calls.count(("image", MATURE)) == 1
    assert ("image", FEATURE) not in catalog.jellyfin.calls

    item = catalog.get(CASEY, f"/api/library-items/{FAMILY_FILM}").json()
    assert item["name"] == "Synthetic Family Film" and item["path"] is None
    assert all(version["path"] is None for version in item["versions"])
    assert catalog.get(CASEY, f"/api/library-items/{FAMILY_FILM}/image").content == (
        f"artwork {FAMILY_FILM}".encode()
    )
    for user in (CASEY, LOCAL):
        children = catalog.get(user, f"/api/library-items/{SERIES}/children").json()
        assert [child["id"] for child in children] == [EPISODE]


def test_a_reachable_parent_keeps_its_restricted_children_hidden(catalog):
    catalog.evidence.allowed.discard(EPISODE)
    children = f"/api/library-items/{SERIES}/children"
    assert catalog.get(CASEY, f"/api/library-items/{SERIES}").status_code == 200
    assert catalog.get(CASEY, children).json() == []
    assert [child["id"] for child in catalog.get(OWNER, children).json()] == [EPISODE]


def _seed_scan(catalog, item_id: str, file_name: str) -> str:
    with catalog.accounts.factory() as db:
        media_item = repository.upsert_media_item(
            db,
            server_id="http://jellyfin.test",
            jellyfin_id=item_id,
            library_id=FAMILY,
            item_type="Movie",
            name=file_name,
            path=f"/jellyfin/family/{file_name}",
        )
        scan = repository.create_scan(
            db,
            media_item=media_item,
            resolved_local_path=str(MEDIA / "family" / file_name),
            media_provider_id="tmdb:9002",
            duration_seconds=100.0,
            scan_audio=True,
            audio_model="whisper",
            audio_filter="better_profanity",
            scan_video=False,
            video_model=None,
            frame_interval=None,
            speed_up=False,
        )
        repository.update_scan_status(db, scan.id, "complete")
        return scan.id


def test_candidates_and_associations_rest_on_server_records(catalog):
    mature = _seed_scan(catalog, MATURE, "Synthetic Mature Film.mkv")
    late = _seed_scan(catalog, "c3000000000000000000000000000003", "Synthetic Late Show.mkv")
    search = {"source": "jellyfin", "item_id": FAMILY_FILM}

    owner = catalog.post(OWNER, "/api/filter-candidates", search)
    assert owner.status_code == 200, owner.text
    assert {match["scan"]["id"] for match in owner.json()["matches"]} == {mature, late}
    casey = catalog.post(CASEY, "/api/filter-candidates", search)
    assert casey.status_code == 200
    assert [match["scan"]["id"] for match in casey.json()["matches"]] == [late]

    assert _refused(catalog.post(CASEY, "/api/filter-candidates", {**search, "item_id": MATURE}))
    claimed = {**search, "source_path": "/jellyfin/family/Synthetic Mature Film.mkv"}
    assert _refused(catalog.post(CASEY, "/api/filter-candidates", claimed))

    media_item_id = casey.json()["media_item_id"]
    association = {"media_item_id": media_item_id, "scan_ids": [mature]}
    assert _refused(catalog.post(CASEY, "/api/filter-candidates/associations", association))
    with catalog.accounts.factory() as db:
        assert db.get(ScanRow, mature).media_item_id != media_item_id
    association["scan_ids"] = [late]
    response = catalog.post(CASEY, "/api/filter-candidates/associations", association)
    assert response.status_code == 204


def test_an_existing_session_observes_scope_and_sync_changes(catalog):
    movies = f"/api/libraries/{MOVIES}/items"
    assert _refused(catalog.get(LOCAL, movies))
    catalog.set_scope(LOCAL, ("jellyfin_library", FAMILY), ("jellyfin_library", MOVIES))
    assert catalog.get(LOCAL, movies).json()["total_count"] == 1

    assert _refused(catalog.get(CASEY, f"/api/library-items/{FEATURE}"))
    catalog.evidence.allowed.add(FEATURE)
    catalog.sync_casey(all_libraries=True, library_ids=frozenset())
    assert catalog.get(CASEY, movies).json()["total_count"] == 1
    assert catalog.get(CASEY, f"/api/library-items/{FEATURE}").status_code == 200

    catalog.evidence.allowed.discard(FAMILY_FILM)
    catalog.sync_casey(all_libraries=True, library_ids=frozenset())
    assert "Synthetic Family Film" not in _names(catalog.get(CASEY, FAMILY_ITEMS).json())


def test_reachable_file_metadata_is_available_when_paths_are_redacted(catalog, monkeypatch):
    original = catalog.jellyfin.item

    def with_versions(item_id):
        item = original(item_id)
        if item_id != FAMILY_FILM:
            return item
        version = JellyfinMediaVersion(id="v0", path=item.path, audio_track_count=2)
        return item.model_copy(update={"versions": [version]})

    monkeypatch.setattr(catalog.jellyfin, "item", with_versions)
    path = f"/api/library-items/{FAMILY_FILM}"
    owner = catalog.get(OWNER, path).json()
    member = catalog.get(CASEY, path).json()
    assert owner["versions"][0]["path"] == owner["path"]
    assert member["path"] is None
    assert member["versions"][0]["path"] is None
    for item in (owner, member):
        version = item["versions"][0]
        assert version["file_name"] == "Synthetic Family Film.mkv"
        assert version["audio_track_count"] == 2


def test_audio_tracks_name_media_without_paths(catalog):
    tracks = "/api/media/audio-tracks"
    path = str(MEDIA / "family" / "Synthetic Family Film.mkv")
    by_path = catalog.get(CASEY, tracks, video_path=path)
    assert by_path.status_code == 422 and path not in by_path.text
    assert _refused(catalog.get(CASEY, tracks, item_id=MATURE))
    reachable = catalog.get(CASEY, tracks, item_id=FAMILY_FILM)
    assert reachable.status_code == 409
    assert reachable.json()["detail"] == "Audio inventory unavailable; refresh the selected file"
    assert catalog.get(OWNER, tracks, item_id=MATURE).status_code == 409

    choice = {
        "selection": {"file_version": "0" * 64, "track": {"stream_index": 0, "disposition": 0}}
    }
    selection = f"{tracks}/selection"
    posted_path = catalog.post(CASEY, selection, {**choice, "video_path": path})
    assert posted_path.status_code == 422
    assert _refused(catalog.post(CASEY, selection, {**choice, "item_id": MATURE}))
    allowed = catalog.post(CASEY, selection, {**choice, "item_id": FAMILY_FILM})
    assert allowed.status_code == 409 and str(MEDIA) not in allowed.text


def test_item_decisions_are_reused_within_one_sync_window(catalog):
    catalog.get(CASEY, FAMILY_ITEMS)
    first = [read for read in catalog.evidence.reads if read.startswith("item:")]
    catalog.get(CASEY, FAMILY_ITEMS)
    catalog.get(CASEY, f"/api/library-items/{FAMILY_FILM}/image")
    assert [read for read in catalog.evidence.reads if read.startswith("item:")] == first
    assert sorted(first) == sorted(
        f"item:{item}" for item in (FAMILY_FILM, MATURE, "c3000000000000000000000000000003")
    )


def test_recover_scans_availability_reads_only_reachable_candidates(catalog, monkeypatch):
    from scanner.persistence.repository import repository_catalog

    def no_hashing(_path):
        raise AssertionError("availability must not hash media")

    monkeypatch.setattr(repository_catalog, "compute_video_md5", no_hashing)
    search = {"source": "jellyfin", "item_id": FAMILY_FILM}
    path = "/api/filter-candidates/available"
    assert catalog.post(OWNER, path, search).json() == {"available": False}
    _seed_scan(catalog, MATURE, "Synthetic Mature Film.mkv")
    assert catalog.post(OWNER, path, search).json() == {"available": True}
    # Casey can't reach the mature film, so nothing is recoverable for them.
    assert catalog.post(CASEY, path, search).json() == {"available": False}
    _seed_scan(catalog, "c3000000000000000000000000000003", "Synthetic Late Show.mkv")
    assert catalog.post(CASEY, path, search).json() == {"available": True}
    assert _refused(catalog.post(CASEY, path, {**search, "item_id": MATURE}))
