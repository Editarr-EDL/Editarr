from importlib import import_module
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from scanner.http import subtitle_publications_api as api
from scanner.modeling.model_subtitles import SubtitleSource
from scanner.runtime.subtitle_sidecars import media_slot, publish_sidecar


def _client(monkeypatch, media, version):
    import_module("scanner.api")
    catalog_files = import_module("scanner.http.catalog_files")
    account = SimpleNamespace(local_export_id=SimpleNamespace(value="catalog-viewer-id"))
    access = SimpleNamespace(principal=SimpleNamespace(account=account))
    viewer = SimpleNamespace(db=object(), resolve=lambda path: path, access=access)

    def source_file(db, authorized_viewer, item_id, *, media_source_id):
        assert db is viewer.db and authorized_viewer is viewer
        if item_id != "movie" or media_source_id != version:
            raise HTTPException(404, "Media unavailable")
        return SimpleNamespace(path=str(media), media_source_id=version)

    monkeypatch.setattr(catalog_files, "source_file", source_file)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api._viewer] = lambda: viewer
    return TestClient(app)


def test_lists_verified_subtitle_without_a_saved_review(tmp_path, monkeypatch):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"video")
    source = SubtitleSource(
        id="a" * 64,
        label="English",
        language="eng",
        format="srt",
        kind="embedded",
        forced=True,
    )
    assert not publish_sidecar(media, media_slot(media, "edition"), source, b"filtered")
    client = _client(monkeypatch, media, "edition")
    response = client.get(
        "/api/media/subtitles/publications",
        params={
            "source": "jellyfin",
            "item_id": "movie",
            "media_source_id": "edition",
        },
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["published_subtitles"] == [
        {
            "filename": f"Movie.Editarr Filtered {'a' * 16}.eng.forced.srt",
            "label": "English · Filtered",
            "format": "srt",
            "forced": True,
            "owned_by_viewer": False,
        }
    ]
    assert str(tmp_path) not in response.text
    denied = client.get(
        "/api/media/subtitles/publications",
        params={
            "source": "jellyfin",
            "item_id": "movie",
            "media_source_id": "other",
        },
    )
    assert denied.status_code == 404
