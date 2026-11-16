"""Naming is display metadata across source admission, workers, and provenance."""

import json
from pathlib import Path

from scanner import api, repository
from scanner.db import (
    SavedReviewRow,
    ScanRow,
)
from scanner.models import (
    JellyfinMediaItem,
    JellyfinMediaVersion,
    JellyfinSettingsUpdate,
)
from tests.jellyfin_item_support import jellyfin_target
from tests.scan_naming_support import (
    _mcf_import_options,
    published_bytes,
    upload_import,
)
from tests.scan_naming_support import (
    client_context as client_context,
)
from tests.scan_naming_support import (
    db as db,
)


def test_upload_import_succeeds_and_import_routes_enforce_auth_origin_and_csrf(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    media_path = tmp_path / "movie.mkv"
    media_path.write_bytes(b"video")
    target = jellyfin_target(monkeypatch, db, media_path)
    options = json.dumps({"name": "Upload", "target": target})
    mcf_options = json.dumps(
        _mcf_import_options("Upload", target, start_seconds=4.02, end_seconds=5040.1)
    )
    mcf_content = (Path(__file__).parent / "fixtures" / "mcf" / "valid-1.1.mcf").read_bytes()
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 6000.0)

    uploaded = client.post(
        "/api/edl-imports/upload",
        files={"file": ("input.edl", b"1 2 1\n", "application/octet-stream")},
        data={"options": options},
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["scan"]["import_source_name"] == "input.edl"
    assert uploaded.json()["review"]["target"]["source"] == "jellyfin"

    requests = [
        (
            "/api/edl-imports/upload",
            {
                "files": {"file": ("input.edl", b"1 2 1\n", "application/octet-stream")},
                "data": {"options": options},
            },
        ),
        (
            "/api/mcf-imports/upload",
            {
                "files": {"file": ("input.mcf", mcf_content, "text/mcf+vtt")},
                "data": {"options": mcf_options},
            },
        ),
    ]
    for route, request_arguments in requests:
        assert client.post(route, **request_arguments).status_code == 401
        missing_csrf = {key: value for key, value in headers.items() if key != "X-CSRF-Token"}
        assert client.post(route, headers=missing_csrf, **request_arguments).status_code == 403
        wrong_origin = {**headers, "Origin": "http://evil.test"}
        assert client.post(route, headers=wrong_origin, **request_arguments).status_code == 403


def test_jellyfin_import_requires_and_uses_selected_physical_version(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    primary = tmp_path / "primary.mkv"
    alternate = tmp_path / "alternate.mkv"
    primary.write_bytes(b"video")
    alternate.write_bytes(b"video")
    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key="saved-server-key"),
    )

    class FakeJellyfinClient:
        def __init__(self, *_credentials):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def item(self, item_id):
            return JellyfinMediaItem(
                id=item_id,
                library_id="movies",
                item_type="Movie",
                name="Example",
                path=str(primary),
                versions=[
                    JellyfinMediaVersion(id="primary", name="Primary", path=str(primary)),
                    JellyfinMediaVersion(id="alternate", name="Alternate", path=str(alternate)),
                ],
            )

    monkeypatch.setattr(api, "JellyfinClient", FakeJellyfinClient)
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 100.0)
    omitted = upload_import(
        client,
        "edl",
        {"name": "Ambiguous", "target": {"source": "jellyfin", "item_id": "movie"}},
        b"1 2 1\n",
        headers=headers,
    )
    assert omitted.status_code == 409
    assert omitted.json()["detail"]["code"] == "media_unavailable"

    selected = upload_import(
        client,
        "edl",
        {
            "name": "Alternate review",
            "target": {"source": "jellyfin", "item_id": "movie", "media_source_id": "alternate"},
        },
        b"1 2 1\n",
        headers=headers,
    )
    assert selected.status_code == 201, selected.text
    payload = selected.json()
    assert payload["review"]["target"] == {
        "source": "jellyfin",
        "item_id": "movie",
        "media_source_id": "alternate",
    }
    scan = db.get(ScanRow, payload["scan"]["id"])
    assert scan is not None
    assert scan.resolved_local_path == str(alternate)
    assert scan.media_source_id == "alternate"


def test_mcf_import_uses_the_catalog_adapter_and_selected_version(
    client_context, tmp_path, db, monkeypatch
):
    client, headers = client_context
    media_path = tmp_path / "jellyfin.mkv"
    media_path.write_bytes(b"video")
    content = (Path(__file__).parent / "fixtures" / "mcf" / "valid-1.1.mcf").read_bytes()
    monkeypatch.setattr(api, "get_video_duration", lambda _path: 6000.0)

    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key="saved-server-key"),
    )

    class FakeClient:
        def __init__(self, *_credentials):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def item(self, item_id):
            return JellyfinMediaItem(
                id=item_id,
                library_id="movies",
                item_type="Movie",
                name="Catalog Movie",
                year=2013,
                versions=[
                    JellyfinMediaVersion(
                        id="file-42",
                        name="Selected Release",
                        path=str(media_path),
                        duration_seconds=6000,
                    )
                ],
            )

    monkeypatch.setattr(api, "JellyfinClient", FakeClient)

    response = upload_import(
        client,
        "mcf",
        _mcf_import_options(
            "Catalog MCF",
            {
                "source": "jellyfin",
                "item_id": "item-7",
                "media_source_id": "file-42",
            },
            start_seconds=4.02,
            end_seconds=5040.1,
        ),
        content,
        headers=headers,
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["review"]["target"]["source"] == "jellyfin"
    assert payload["review"]["target"]["media_source_id"] == "file-42"
    assert payload["review"]["mcf_document"]["title"] == "Ozymandias"
    assert payload["notices"][0]["fields"] == ["title", "work_type", "release"]
    scan = db.get(ScanRow, payload["scan"]["id"])
    assert scan.resolved_local_path == str(media_path)
    assert scan.media_source_id == "file-42"
    exported = client.post(
        f"/api/reviews/{payload['review']['id']}/mcf",
        json={"revision": 0},
        headers=headers,
    )
    assert exported.status_code == 200, exported.text
    assert published_bytes(db, payload["review"]["id"], "mcf")


def test_catalog_review_list_uses_adapter_resolved_version(db, monkeypatch):
    media = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path=None,
    )
    db.add_all(
        [
            SavedReviewRow(
                id="default-review",
                media_item_id=media.id,
                media_source_id="",
                name="Default",
            ),
            SavedReviewRow(
                id="version-review",
                media_item_id=media.id,
                media_source_id="v2",
                name="Version",
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(
        api,
        "_catalog_review_media",
        lambda _db, **_kwargs: (media, "v2", None),
    )

    reviews = api._list_catalog_reviews(
        db,
        source="jellyfin",
        item_id="movie",
        media_source_id=None,
        sort="updated_at",
        direction="desc",
    )

    assert [review.id for review in reviews] == ["version-review"]
    assert reviews[0].target.media_source_id == "v2"
