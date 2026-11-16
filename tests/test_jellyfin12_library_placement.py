"""A library-scoped local account works on items Jellyfin 12 reports without `LibraryId`.

Jellyfin 12.0.0 answers `/Items?Ids=` with a `BaseItemDto` that carries
`ParentId` but no `LibraryId`. The live journey observed this.
The fixture below serves that shape and
`/Library/VirtualFolders` through the real `JellyfinClient`, so the library
Editarr records is the one whose folder holds the item, the same placement that
authorizes a local account's catalog reads.
"""

from __future__ import annotations

import json

import httpx
import pytest

import scanner.api as root
from scanner.db import MediaItemRow, ScanRow
from scanner.http import (
    audio_tracks_api,
    catalog_library_api,
    catalog_targets,
    review_import_routes_api,
)
from scanner.integrations.jellyfin import JellyfinClient
from tests.multi_user_access_support import OWNER, PEER_USER, USER, live_accounts
from tests.scan_access_http import JELLYFIN, LIBRARY_A, LIBRARY_B, serve_scans

FILM_A = "1" * 32
FILM_B = "2" * 32
OUTSIDE = "3" * 32


def _jellyfin12_item(item_id: str, path: str) -> dict[str, object]:
    """A Jellyfin 12.0.0 `/Items?Ids=` record: `ParentId` is the folder, no `LibraryId`."""

    return {
        "Id": item_id,
        "Name": f"Film {item_id[:4]}",
        "Type": "Movie",
        "MediaType": "Video",
        "Path": path,
        "ParentId": "f" * 32,
        "RunTimeTicks": 600_000_000,
        "MediaSources": [{"Id": item_id, "Path": path, "Name": f"Film {item_id[:4]}"}],
    }


class Jellyfin12:
    """`/Items?Ids=` and `/Library/VirtualFolders` as Jellyfin 12.0.0 answers them."""

    def __init__(self, records: list[dict[str, object]], folders: list[dict[str, object]]):
        self.records, self.folders, self.paths = records, folders, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == "/Library/VirtualFolders":
            return httpx.Response(200, json=self.folders)
        assert request.url.path == "/Items"
        wanted = request.url.params["Ids"].split(",")
        found = [record for record in self.records if record["Id"] in wanted]
        return httpx.Response(
            200, json={"Items": found, "TotalRecordCount": len(found), "StartIndex": 0}
        )

    def client(self, server_url: str = JELLYFIN, api_key: str = "key-1") -> JellyfinClient:
        return JellyfinClient(
            server_url, api_key, httpx.Client(transport=httpx.MockTransport(self))
        )


def _server(root_dir) -> Jellyfin12:
    paths = {}
    for name, item_id in (("a", FILM_A), ("b", FILM_B), ("elsewhere", OUTSIDE)):
        (root_dir / name).mkdir(exist_ok=True)
        paths[item_id] = root_dir / name / f"{item_id}.mkv"
        paths[item_id].write_bytes(b"synthetic video bytes")
    folders = [
        {"Name": "Movies", "ItemId": LIBRARY_A, "Locations": [str(root_dir / "a")]},
        {"Name": "Family", "ItemId": LIBRARY_B, "Locations": [str(root_dir / "b")]},
    ]
    records = [_jellyfin12_item(item_id, str(path)) for item_id, path in paths.items()]
    return Jellyfin12(records, folders)


def test_an_item_without_library_id_is_placed_in_the_library_holding_its_file(tmp_path):
    server = _server(tmp_path)

    assert server.client().item(FILM_A).library_id == LIBRARY_A
    assert server.client().item(FILM_B).library_id == LIBRARY_B
    assert not server.client().item(OUTSIDE).library_id


@pytest.fixture
def app(monkeypatch, tmp_path):
    catalog_targets.jellyfin_libraries.clear()
    root.JELLYFIN_ITEM_CACHE.clear()
    accounts = live_accounts(tmp_path)
    served = serve_scans(monkeypatch, accounts, tmp_path)
    served.jellyfin12 = _server(served.media)
    monkeypatch.setattr(root, "JellyfinClient", served.jellyfin12.client)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60.0)
    served.client.app.include_router(review_import_routes_api.router)
    served.client.app.include_router(audio_tracks_api.router)
    served.client.app.include_router(catalog_library_api.router)
    served.limit_to_library(USER, LIBRARY_A)
    yield served
    catalog_targets.jellyfin_libraries.clear()
    root.JELLYFIN_ITEM_CACHE.clear()
    served.client.close()
    accounts.close()


def _refused(response) -> bool:
    return response.status_code == 404 and response.json()["code"] == "not_found"


def _scan(app, user_id: int, item_id: str):
    body = {"jellyfin_item_id": item_id, "scan_audio": False, "scan_video": True}
    return app.send("POST", "/api/scans", app.accounts.unsafe_cookie(user_id), body)


def test_a_scoped_local_account_scans_its_library_and_not_another(app):
    created = _scan(app, USER, FILM_A)
    assert created.status_code == 201, created.text
    with app.accounts.factory() as db:
        media = db.get(MediaItemRow, db.get(ScanRow, created.json()["id"]).media_item_id)
        assert media.library_id == LIBRARY_A

    assert _refused(_scan(app, USER, FILM_B))
    assert _scan(app, OWNER, FILM_B).status_code == 201


def _import(app, item_id: str):
    app.client.cookies.clear()
    options = {"target": {"source": "jellyfin", "item_id": item_id}, "name": "placed"}
    return app.client.post(
        "/api/edl-imports/upload",
        headers=app.accounts.unsafe_cookie(USER),
        data={"options": json.dumps(options)},
        files={"file": ("placed.edl", b"0.2 0.8 1\n")},
    )


def test_a_scoped_local_account_imports_onto_its_library_and_not_another(app):
    imported = _import(app, FILM_A)
    assert imported.status_code == 201, imported.text
    assert _refused(_import(app, FILM_B))


def test_a_scoped_local_account_reads_tracks_in_its_library_and_not_another(app):
    def tracks(item_id: str):
        query = {"source": "jellyfin", "item_id": item_id}
        app.client.cookies.clear()
        return app.client.get(
            "/api/media/audio-tracks", params=query, headers=app.accounts.cookie(USER)
        )

    reachable = tracks(FILM_A)
    assert reachable.status_code == 409, reachable.text
    assert reachable.json()["detail"] == "Audio inventory unavailable; refresh the selected file"
    assert _refused(tracks(FILM_B))


def _read(app, user_id: int, item_id: str):
    app.client.cookies.clear()
    return app.client.get(f"/api/library-items/{item_id}", headers=app.accounts.cookie(user_id))


def test_a_path_with_dot_segments_is_in_no_library(app):
    dotted = str(app.media / "a" / ".." / "b" / f"{FILM_B}.mkv")
    record = next(record for record in app.jellyfin12.records if record["Id"] == FILM_B)
    record["Path"] = record["MediaSources"][0]["Path"] = dotted
    app.limit_to_library(PEER_USER, LIBRARY_B)

    for scoped in (USER, PEER_USER):
        assert _refused(_read(app, scoped, FILM_B))
        assert _refused(_scan(app, scoped, FILM_B))

    assert _read(app, OWNER, FILM_B).status_code == 200
    assert _scan(app, OWNER, FILM_B).status_code == 201


CRAFTED = "4" * 32


def test_a_backslash_in_a_posix_folder_name_keeps_the_item_in_its_library(app):
    crafted_dir = app.media / "a" / "x\\..\\..\\b"
    crafted_dir.mkdir()
    (app.media / "a" / "x").mkdir()
    physical = crafted_dir / f"{CRAFTED}.mkv"
    physical.write_bytes(b"library A bytes")
    (app.media / "b" / f"{CRAFTED}.mkv").write_bytes(b"library B bytes")
    assert physical.parent.parent == app.media / "a"
    app.jellyfin12.records.append(_jellyfin12_item(CRAFTED, str(physical)))
    app.limit_to_library(PEER_USER, LIBRARY_B)

    assert _read(app, USER, CRAFTED).status_code == 200
    assert _refused(_read(app, PEER_USER, CRAFTED))
    assert _refused(_scan(app, PEER_USER, CRAFTED))


def test_a_scan_of_a_backslash_posix_folder_reads_the_file_in_its_own_library(app):
    crafted_dir = app.media / "a" / "x\\..\\..\\b"
    crafted_dir.mkdir()
    (app.media / "a" / "x").mkdir()
    physical = crafted_dir / f"{CRAFTED}.mkv"
    physical.write_bytes(b"library A bytes")
    (app.media / "b" / f"{CRAFTED}.mkv").write_bytes(b"library B bytes")
    app.jellyfin12.records.append(_jellyfin12_item(CRAFTED, str(physical)))

    created = _scan(app, USER, CRAFTED)
    assert created.status_code == 201, created.text
    with app.accounts.factory() as db:
        scan = db.get(ScanRow, created.json()["id"])
        assert scan.resolved_local_path == str(physical.resolve())
        assert db.get(MediaItemRow, scan.media_item_id).library_id == LIBRARY_A
