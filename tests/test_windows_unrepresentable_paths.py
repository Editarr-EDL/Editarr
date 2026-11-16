# ruff: noqa: F401, F811
"""A server file name a Windows host cannot spell affects only that file.

A POSIX media server may hold a name with `\\` in it. On a Windows host that
name would split into folders, so the resolver refuses it. Each test patches
the resolver's host-path flavour to `ntpath` and drives the real HTTP route:
the collection or page still answers, and only the unrepresentable item,
version, folder, or event is unresolved.
"""

import ntpath
from pathlib import PureWindowsPath

import pytest
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityImportRow,
    CommunityOperationRow,
    MediaItemRow,
    PathMappingRow,
    SavedReviewRow,
)
from scanner.http import catalog_access
from scanner.integrations.community.transport import CommunityFailure
from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.persistence.repository import repository_paths
from scanner.persistence.repository.community_imports import import_verified_download
from scanner.security.access.identity import CredentialKind
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import stage_event
from tests import catalog_access_http as fixture
from tests.catalog_access_http import LOCAL
from tests.community_identity_support import only_identity_ref
from tests.media_access_support import FAMILY, FAMILY_FILM, FEATURE, LATE_SHOW, MOVIES
from tests.multi_user_access_support import ADMIN, OWNER, USER
from tests.test_audit_history_http import NOW, get, history
from tests.test_catalog_authorization_http import catalog
from tests.test_community_verified_import import (
    BOUNDS,
    import_database,
    local_target,
    verified_input,
)
from tests.test_operations_access_contracts import start
from tests.test_review_access_http import app as review_app
from tests.test_review_access_http import seeded_review

FAMILY_ITEMS = f"/api/libraries/{FAMILY}/items"


@pytest.fixture
def windows(monkeypatch):
    def host(client=None):
        monkeypatch.setattr(repository_paths, "_HOST_PATHS", ntpath)
        if client is not None:
            client._transport.raise_server_exceptions = False

    return host


def _ids(page) -> set[str]:
    return {item["id"] for item in page["items"]}


def _bad_jellyfin_path(monkeypatch, item_id: str, path: str) -> None:
    original = fixture.JELLYFIN_ITEMS[item_id]
    bad = original.model_copy(update={"path": path, "versions": []})
    monkeypatch.setitem(fixture.JELLYFIN_ITEMS, item_id, bad)
    _reread()


def _reread() -> None:
    fixture._clear_caches()
    catalog_access.evidence_cache._copies.clear()


def test_one_unnameable_item_leaves_the_rest_of_its_library_listed(catalog, monkeypatch, windows):
    before = {
        actor: _ids(catalog.get(actor, FAMILY_ITEMS).json()) for actor in (LOCAL, USER, OWNER)
    }
    assert FAMILY_FILM in before[LOCAL] and FAMILY_FILM in before[USER]
    _bad_jellyfin_path(monkeypatch, FAMILY_FILM, "/jellyfin/family/a\\b.mkv")
    windows(catalog.client)

    for actor in (LOCAL, USER):
        response = catalog.get(actor, FAMILY_ITEMS)
        assert response.status_code == 200, (actor, response.text)
        assert _ids(response.json()) == before[actor] - {FAMILY_FILM}
        detail = catalog.get(actor, f"/api/library-items/{FAMILY_FILM}")
        assert (detail.status_code, detail.json()["code"]) == (404, "not_found")
        assert catalog.get(actor, f"/api/library-items/{LATE_SHOW}").status_code == 200
    owner = catalog.get(OWNER, FAMILY_ITEMS)
    assert owner.status_code == 200 and _ids(owner.json()) == before[OWNER]


def test_an_unnameable_item_elsewhere_on_the_server_leaves_linked_reads_intact(
    catalog, monkeypatch, windows
):
    before = _ids(catalog.get(USER, FAMILY_ITEMS).json())
    _bad_jellyfin_path(monkeypatch, FEATURE, "/jellyfin/movies/a\\b.mkv")
    windows(catalog.client)

    for actor in (USER, LOCAL, OWNER):
        assert catalog.get(actor, FAMILY_ITEMS).status_code == 200
        assert catalog.get(actor, f"/api/library-items/{FAMILY_FILM}").status_code == 200
    assert _ids(catalog.get(USER, FAMILY_ITEMS).json()) == before
    assert catalog.get(USER, f"/api/libraries/{MOVIES}/items").status_code == 404


def test_an_unnameable_library_folder_leaves_source_listings_intact(catalog, monkeypatch, windows):
    before = {actor: catalog.get(actor, FAMILY_ITEMS).json() for actor in (USER, LOCAL)}
    bad = {"ItemId": "f" * 32, "Name": "Bad", "Locations": ["/jellyfin/bad\\name"]}
    folders = [*fixture.VIRTUAL_FOLDERS, bad]
    monkeypatch.setattr(
        catalog.evidence,
        "library_folders",
        lambda server: parse_library_folders(folders, server, catalog.clock()),
    )
    _reread()
    windows(catalog.client)

    for actor in (USER, LOCAL):
        response = catalog.get(actor, FAMILY_ITEMS)
        assert response.status_code == 200, (actor, response.text)
        assert response.json() == before[actor]


def test_an_event_with_unnameable_media_leaves_the_audit_pages_intact(history, windows):
    client, accounts, identifiers = history
    totals = {actor: get(history, actor, "/api/audit").json()["total"] for actor in (OWNER, ADMIN)}
    with accounts.factory() as db:
        db.add(
            MediaItemRow(
                id="bad",
                server_id="http://jellyfin.test",
                jellyfin_id="c3000000000000000000000000000009",
                item_type="Movie",
                name="bad",
                path="/media/a\\b.mkv",
            )
        )
        db.flush()
        event = AuditEvent(
            NOW,
            OWNER,
            CredentialKind.SESSION,
            AuditAction.EXPORT_PUBLISH,
            AuditOutcome.SUCCEEDED,
            target_user=USER,
            media_key="bad",
            resource="review-1",
            details={"format": "edl", "target_kind": "personal"},
        )
        bad = stage_event(db, event)
        db.commit()
    windows(client)

    for actor in (OWNER, ADMIN):
        filtered = get(history, actor, "/api/audit", page_size=1, media_key="unrelated")
        assert filtered.status_code == 200, (actor, filtered.text)
        assert filtered.json()["total"] == 0
        first = get(history, actor, "/api/audit", page_size=1).json()
        second = get(history, actor, "/api/audit", page_size=1, page=2)
        assert second.status_code == 200 and second.json()["total"] == first["total"]
        assert len(second.json()["events"]) == min(1, first["total"] - 1)
        detail = get(history, actor, f"/api/audit/{identifiers[USER]}")
        assert detail.status_code == 200
    assert get(history, OWNER, "/api/audit").json()["total"] == totals[OWNER] + 1
    assert get(history, OWNER, f"/api/audit/{bad}").status_code == 200
    assert get(history, ADMIN, "/api/audit").json()["total"] == totals[ADMIN]
    hidden = get(history, ADMIN, f"/api/audit/{bad}")
    assert (hidden.status_code, hidden.json()["code"]) == (404, "not_found")


def test_audio_for_an_unnameable_file_is_unavailable(catalog, monkeypatch, windows):
    _bad_jellyfin_path(monkeypatch, FAMILY_FILM, "/jellyfin/family/a\\b.mkv")
    windows(catalog.client)
    tracks = "/api/media/audio-tracks"

    inventory = catalog.get(OWNER, tracks, item_id=FAMILY_FILM)
    assert inventory.status_code == 409, inventory.text
    assert inventory.json()["detail"] == "Audio inventory unavailable; refresh the selected file"
    choice = {
        "selection": {"file_version": "0" * 64, "track": {"stream_index": 0, "disposition": 0}},
        "item_id": FAMILY_FILM,
    }
    selected = catalog.post(OWNER, f"{tracks}/selection", choice)
    assert selected.status_code == 409, selected.text
    assert selected.json()["detail"] == "Audio inventory unavailable; refresh the selected file"


def test_a_review_of_an_unnameable_file_reports_its_media_unavailable(review_app, windows):
    from scanner.http import review_repair_api

    review = seeded_review(review_app, OWNER, "Bad Windows target")
    review_app.client.app.include_router(review_repair_api.router)
    with review_app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        item_id = row.media_item.jellyfin_id
        row.media_item.path = "/media/a\\b.mkv"
        db.commit()
    original = review_app.items[item_id]
    review_app.items[item_id] = original.model_copy(
        update={"path": "/media/a\\b.mkv", "versions": []}
    )
    windows(review_app.client)

    owner = review_app.accounts.unsafe_cookie(OWNER)
    for method, path in [
        ("GET", f"/api/library-items/{item_id}/reviews"),
        ("POST", f"/api/reviews/{review.id}/repair/recover"),
    ]:
        response = review_app.send(method, path, owner)
        assert response.status_code == 409, (method, path, response.text)
        assert response.json()["detail"]["code"] == "media_unavailable"


def test_a_stale_unnameable_stored_path_refuses_only_that_community_import(
    import_database, tmp_path, monkeypatch
):
    downloaded = verified_input()
    target = local_target(downloaded, tmp_path)
    with Session(import_database) as db:
        media = db.get(MediaItemRow, target.media_item_id)
        media.path = "/media/a\\b.mkv"
        db.commit()
        before = db.query(CommunityOperationRow).count()
        monkeypatch.setattr(repository_paths, "_HOST_PATHS", ntpath)
        with pytest.raises(CommunityFailure) as refused:
            import_verified_download(
                db,
                downloaded,
                target,
                bounds=BOUNDS,
                confirm_uncertain=True,
                credential_ref=only_identity_ref(db),
            )
        assert refused.value.code == "invalid_target"
        assert db.query(CommunityImportRow).count() == 0
        assert db.query(CommunityOperationRow).count() == before


def test_a_mapping_may_replace_a_remote_folder_name_holding_a_backslash(start, tmp_path, windows):
    app = start()
    local = tmp_path / "A"
    admin = app.accounts.unsafe_cookie(OWNER)
    created = app.send(
        "POST",
        "/api/settings/path-mappings",
        admin,
        {"remote_path": "/remote/a\\b", "local_path": str(local)},
    )
    assert created.status_code == 200, created.text
    windows(app.client)

    resolved = app.send(
        "POST",
        "/api/settings/path-mappings/resolve",
        admin,
        {"remote_path": "/remote/a\\b/film.mkv"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["local_path"] == str(local / "film.mkv")
    assert resolved.json()["mapping_id"] == created.json()["id"]
    refused = app.send(
        "POST",
        "/api/settings/path-mappings/resolve",
        admin,
        {"remote_path": "/remote/a\\b/c\\d.mkv"},
    )
    assert refused.status_code == 400


def test_a_windows_host_joins_the_mapped_folder_in_its_own_spelling(import_database, monkeypatch):
    monkeypatch.setattr(repository_paths, "_HOST_PATHS", ntpath)
    monkeypatch.setattr(repository_paths, "Path", PureWindowsPath)
    with Session(import_database) as db:
        db.add(PathMappingRow(remote_path="/remote/a\\b", local_path="C:/Media/A", sort_order=0))
        db.commit()
        resolution = repository_paths.resolve_local_path(db, "/remote/a\\b/film.mkv")
        assert resolution.local_path == "C:\\Media\\A\\film.mkv"
        with pytest.raises(repository_paths.UnrepresentablePath):
            repository_paths.resolve_local_path(db, "/remote/a\\b/c\\d.mkv")
        with pytest.raises(repository_paths.UnrepresentablePath):
            repository_paths.resolve_local_path(db, "/elsewhere/c\\d.mkv")
