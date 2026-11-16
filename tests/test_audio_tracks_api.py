from dataclasses import asdict

import pytest

import scanner.api  # noqa: F401 - the app module loads before its routers
from scanner.db import get_db
from scanner.http import audio_tracks_api
from scanner.pipeline.audio_tracks import AudioTrack, AudioTrackInventory
from scanner.security import auth_policy
from tests.jellyfin_item_support import jellyfin_target
from tests.test_auth_policy import _session_headers
from tests.test_auth_policy import policy_app as policy_app


@pytest.fixture
def audio_api(policy_app, tmp_path, monkeypatch):
    client, _, issued = policy_app
    client.app.include_router(audio_tracks_api.router)

    def database():
        with auth_policy.get_session_factory()() as db:
            yield db

    client.app.dependency_overrides[get_db] = database
    source = tmp_path / "file.mkv"
    source.write_bytes(b"generated")
    inventory = AudioTrackInventory(
        "a" * 64,
        (
            AudioTrack(1, "eng", "Main", "aac", 2, 0, 0.0, 1.0, "1/48000"),
            AudioTrack(2, "eng", "Commentary", "aac", 2, 0, 0.0, 1.0, "1/48000"),
        ),
    )
    monkeypatch.setattr(audio_tracks_api, "probe_audio_tracks", lambda _path: inventory)
    with auth_policy.get_session_factory()() as db:
        target = jellyfin_target(monkeypatch, db, source)
    return client, issued, source, inventory, {"item_id": target["item_id"]}


def test_inventory_is_protected_and_ambiguous_tracks_require_choice(audio_api):
    client, issued, _, _, item = audio_api
    assert client.get("/api/media/audio-tracks", params=item).status_code == 401
    response = client.get(
        "/api/media/audio-tracks",
        params=item,
        headers=_session_headers(issued),
    )
    assert response.status_code == 200
    assert response.json()["selection"] is None
    assert [track["title"] for track in response.json()["tracks"]] == ["Main", "Commentary"]


def test_selection_requires_csrf_and_rejects_changed_snapshot(audio_api):
    client, issued, _, inventory, item = audio_api
    payload = item | {"selection": asdict(inventory.select(2))}
    headers = _session_headers(issued)
    assert (
        client.post("/api/media/audio-tracks/selection", json=payload, headers=headers).status_code
        == 403
    )
    headers["X-CSRF-Token"] = issued.csrf_token
    response = client.post("/api/media/audio-tracks/selection", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json() == payload["selection"]
    remembered = client.get("/api/media/audio-tracks", params=item, headers=headers)
    assert remembered.json()["selection"] == payload["selection"]
    payload["selection"]["track"]["title"] = "Different"
    assert (
        client.post("/api/media/audio-tracks/selection", json=payload, headers=headers).status_code
        == 409
    )
    payload["selection"]["file_version"] = "b" * 64
    assert (
        client.post("/api/media/audio-tracks/selection", json=payload, headers=headers).status_code
        == 409
    )


def test_audio_admission_requires_exact_selection_but_video_does_not(audio_api):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from scanner.modeling.model_audio_tracks import AudioSelection

    _, _, source, inventory, _ = audio_api
    with pytest.raises(HTTPException) as failure:
        audio_tracks_api.require_audio_selection(
            SimpleNamespace(scan_audio=True, audio_selection=None), str(source)
        )
    assert failure.value.status_code == 409
    audio_tracks_api.require_audio_selection(SimpleNamespace(scan_audio=False), str(source))
    audio_tracks_api.require_audio_selection(
        SimpleNamespace(
            scan_audio=True, audio_selection=AudioSelection.from_selection(inventory.select(2))
        ),
        str(source),
    )


def test_stale_memory_never_preselects_a_new_default(audio_api, monkeypatch):
    from dataclasses import replace

    client, issued, _, inventory, item = audio_api
    headers = {**_session_headers(issued), "X-CSRF-Token": issued.csrf_token}
    payload = item | {"selection": asdict(inventory.select(2))}
    assert (
        client.post("/api/media/audio-tracks/selection", json=payload, headers=headers).status_code
        == 200
    )
    changed = replace(
        inventory, file_version="b" * 64, tracks=(replace(inventory.tracks[0], disposition=1),)
    )
    monkeypatch.setattr(audio_tracks_api, "probe_audio_tracks", lambda _path: changed)
    response = client.get("/api/media/audio-tracks", params=item, headers=headers)
    assert response.json()["selection"] is None
    assert response.json()["selection_stale"] is True
