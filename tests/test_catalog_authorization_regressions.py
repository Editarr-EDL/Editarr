"""Regression coverage for the MU-20 independent review findings."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from scanner.db import ScanRow
from scanner.integrations.jellyfin import JellyfinClient
from scanner.integrations.jellyfin_user_snapshot import Unverified, UnverifiedReason
from tests.media_access_support import FAMILY_FILM, LATE_SHOW, MATURE, MEDIA, MOVIES
from tests.multi_user_access_support import OWNER, USER
from tests.test_catalog_authorization_http import _seed_scan
from tests.test_catalog_authorization_http import catalog as catalog


def signature(response):
    return response.status_code, response.content, dict(response.headers)


def test_sync_revocation_survives_an_item_read_outage(catalog, monkeypatch):
    path = f"/api/library-items/{FAMILY_FILM}"
    assert catalog.get(USER, path).status_code == 200
    catalog.evidence.allowed.discard(FAMILY_FILM)
    catalog.sync_casey(all_libraries=False, library_ids=frozenset())
    monkeypatch.setattr(
        catalog.evidence,
        "item_access",
        lambda *_args: Unverified(UnverifiedReason.UNREACHABLE),
    )
    assert catalog.get(USER, path).status_code == 404


def test_fresh_item_answer_may_complete_after_the_request_clock_sample(catalog, monkeypatch):
    original = catalog.evidence.item_access

    def item_access(identity, item_ids):
        return replace(
            original(identity, item_ids),
            verified_at=catalog.clock.now + timedelta(milliseconds=1),
        )

    monkeypatch.setattr(catalog.evidence, "item_access", item_access)
    assert catalog.get(USER, f"/api/library-items/{FAMILY_FILM}").status_code == 200


@pytest.mark.parametrize("suffix", ["", "/image", "/children", "/seasons"])
def test_missing_jellyfin_id_is_indistinguishable_from_hidden(catalog, monkeypatch, suffix):
    original = catalog.jellyfin.item
    absent = "ffffffffffffffffffffffffffffffff"

    def item(item_id):
        if item_id != absent:
            return original(item_id)
        with httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"Items": []}))
        ) as transport:
            return JellyfinClient("http://jellyfin.test", "synthetic", transport).item(item_id)

    monkeypatch.setattr(catalog.jellyfin, "item", item)
    assert signature(catalog.get(USER, f"/api/library-items/{MATURE}{suffix}")) == signature(
        catalog.get(USER, f"/api/library-items/{absent}{suffix}")
    )


def _jellyfin_answers(catalog, monkeypatch, answer) -> None:
    """Serve library listings and item reads through the real client over `answer`."""

    def client():
        return JellyfinClient(
            "http://jellyfin.test", "synthetic", httpx.Client(transport=httpx.MockTransport(answer))
        )

    monkeypatch.setattr(
        catalog.jellyfin, "all_items", lambda library_id, **kw: client().all_items(library_id, **kw)
    )
    monkeypatch.setattr(catalog.jellyfin, "item", lambda item_id: client().item(item_id))


@pytest.mark.parametrize("status", [400, 404])
def test_an_id_jellyfin_rejects_is_indistinguishable_from_hidden(catalog, monkeypatch, status):
    hidden_library = catalog.get(USER, f"/api/libraries/{MOVIES}/items")
    hidden_item = catalog.get(USER, f"/api/library-items/{MATURE}")
    _jellyfin_answers(catalog, monkeypatch, lambda _request: httpx.Response(status, json={}))

    unknown = "0" * 32
    library = catalog.get(OWNER, f"/api/libraries/{unknown}/items")
    assert signature(library) == signature(hidden_library)
    assert library.json() == {"code": "not_found", "detail": "Not found"}
    assert signature(catalog.get(OWNER, f"/api/library-items/{unknown}")) == signature(hidden_item)


@pytest.mark.parametrize(
    "failure",
    [
        lambda _request: httpx.Response(500, json={}),
        lambda _request: httpx.Response(503, json={}),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request)),
    ],
    ids=["500", "503", "connection"],
)
def test_jellyfin_failures_browsing_a_library_stay_bad_gateway(catalog, monkeypatch, failure):
    _jellyfin_answers(catalog, monkeypatch, failure)

    assert catalog.get(OWNER, f"/api/libraries/{'0' * 32}/items").status_code == 502
    assert catalog.get(OWNER, f"/api/library-items/{'0' * 32}").status_code == 502


def test_a_path_addressed_audio_request_is_rejected_without_inspecting_files(catalog, monkeypatch):
    from scanner.http import audio_tracks_api

    def unexpected_file_read(*_args):
        pytest.fail("A path-addressed request inspected the filesystem")

    monkeypatch.setattr(audio_tracks_api, "_file", unexpected_file_read)
    for name in ("Synthetic Family Film.mkv", "does-not-exist.mkv"):
        response = catalog.get(
            OWNER, "/api/media/audio-tracks", video_path=str(MEDIA / "family" / name)
        )
        assert response.status_code == 422


def test_candidates_redact_stored_error_text(catalog):
    scan_id = _seed_scan(catalog, LATE_SHOW, "Synthetic Late Show.mkv")
    private_path = "/mnt/private-media/household/Synthetic Family Film.mkv"
    with catalog.accounts.factory() as db:
        db.get(ScanRow, scan_id).error_message = f"Cannot read {private_path}"
        db.commit()
    result = catalog.post(
        USER, "/api/filter-candidates", {"source": "jellyfin", "item_id": FAMILY_FILM}
    )
    assert result.status_code == 200
    assert any(match["scan"]["id"] == scan_id for match in result.json()["matches"])
    assert private_path not in result.text


def test_protected_artwork_is_not_stored_by_the_browser(catalog):
    response = catalog.get(OWNER, f"/api/library-items/{FAMILY_FILM}/image")
    assert response.status_code == 200
    assert "private" in response.headers["cache-control"]
    assert "no-store" in response.headers["cache-control"]


def test_artwork_urls_and_etags_are_principal_scoped(catalog):
    path = f"/api/library-items/{FAMILY_FILM}"
    owner = catalog.get(OWNER, path).json()
    user = catalog.get(USER, path).json()
    assert owner["image_url"] != user["image_url"]
    assert "?" in owner["image_url"] and "?" in user["image_url"]
    owner_image = catalog.get(OWNER, owner["image_url"])
    user_image = catalog.get(USER, user["image_url"])
    assert owner_image.status_code == user_image.status_code == 200
    assert owner_image.headers["etag"] != user_image.headers["etag"]
    assert "cookie" in owner_image.headers["vary"].lower()


def test_item_evidence_expires_even_if_the_browser_session_survives(catalog):
    path = f"/api/library-items/{FAMILY_FILM}"
    assert catalog.get(USER, path).status_code == 200
    catalog.clock.now += timedelta(hours=24, seconds=1)
    assert catalog.get(USER, path).status_code == 403
