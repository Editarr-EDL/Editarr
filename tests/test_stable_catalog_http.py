"""A legacy Jellyfin bookmark keeps its history after the file is replaced."""

from hashlib import sha256
from pathlib import Path

import pytest

from scanner import api, repository
from scanner.db import SavedReviewRow, ScanRow
from scanner.http import catalog_targets, stable_catalog_api
from scanner.integrations.jellyfin import JellyfinNotFound
from scanner.modeling.media_identity import normalize_jellyfin
from scanner.models import JellyfinLibrary
from scanner.persistence.repository import media_identity_store
from scanner.persistence.repository.review_export_target import _review_export_scans
from scanner.runtime.media_fingerprints import remember_content_digest
from scanner.runtime.scan_version import file_generation
from tests.multi_user_access_support import OWNER, USER, live_accounts
from tests.scan_access_http import LIBRARY_A, LIBRARY_B, guid, serve_scans

_TITLE = "The Life of Chuck"
_REPLACEMENT_ID = "042dd80f83ffbe3eea155c3af5cbc091"


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    app = serve_scans(monkeypatch, accounts, tmp_path)
    app.client.app.include_router(stable_catalog_api.router)
    catalog_targets.jellyfin_libraries.clear()
    with api.LIBRARY_ITEMS_CACHE_LOCK:
        api.JELLYFIN_ITEM_CACHE.clear()
    client = api.JellyfinClient

    def item(item_id):
        try:
            return app.items[item_id]
        except KeyError as exc:
            raise JellyfinNotFound("Item not found") from exc

    monkeypatch.setattr(client, "item", item)
    monkeypatch.setattr(
        client,
        "all_items",
        lambda library_id, **kwargs: [
            row
            for row in app.items.values()
            if row.library_id == library_id and kwargs["search_term"] in row.name
        ],
        raising=False,
    )
    monkeypatch.setattr(
        client,
        "libraries",
        lambda: [JellyfinLibrary(id=LIBRARY_A, name="Movies", locations=[str(app.media)])],
        raising=False,
    )
    app.limit_to_library(USER, LIBRARY_B)
    yield app
    catalog_targets.jellyfin_libraries.clear()
    app.client.close()
    accounts.close()


def test_old_jellyfin_bookmark_recovers_title_but_keeps_the_original_version(catalog):
    historical_scan_id, _ = catalog.seed_scan(OWNER, _TITLE, status="complete")
    review_id = catalog.seed_review(OWNER, "Original scan review", historical_scan_id)
    metadata = normalize_jellyfin({"Type": "Movie", "ProviderIds": {"Tmdb": "842924"}})
    with catalog.accounts.factory() as db:
        scan = db.get(ScanRow, historical_scan_id)
        original_file = Path(scan.source_path)
        original_file.write_bytes(b"original cut")
        scan.duration_seconds = 6644.471
        old_item_id = scan.media_item.jellyfin_id
        assert old_item_id == guid(_TITLE)
        title_id = media_identity_store.observe_title(db, scan.media_item, metadata)
        original_version_id = media_identity_store.observe_version(
            db,
            media=scan.media_item,
            media_source_id=None,
            source_path=scan.source_path,
            resolved_path=scan.resolved_local_path,
            content_sha256=sha256(original_file.read_bytes()).hexdigest(),
        )
        media_identity_store.bind_scan_version(db, historical_scan_id, original_version_id)
        media_identity_store.bind_review_version(db, review_id, original_version_id)
        db.commit()

    original_file.unlink()
    catalog.jellyfin_item(_REPLACEMENT_ID, LIBRARY_A)
    replacement = catalog.items[_REPLACEMENT_ID].model_copy(
        update={"name": _TITLE, "duration_seconds": 6644.480, "media_identity": metadata}
    )
    catalog.items[_REPLACEMENT_ID] = replacement
    owner = catalog.accounts.cookie(OWNER)
    old_url = f"/api/editarr-items/legacy/{old_item_id}?library_id={LIBRARY_A}"
    response = catalog.send("GET", old_url, owner)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["title_id"] == title_id
    assert result["active_item"]["id"] == _REPLACEMENT_ID
    assert result["selected_version_id"] == original_version_id
    assert len(result["versions"]) == 1
    original = result["versions"][0]
    assert original["id"] == original_version_id
    assert original["jellyfin_item_id"] == old_item_id
    assert original["available"] is False
    assert [scan["id"] for scan in original["scans"]] == [historical_scan_id]
    assert [review["id"] for review in original["reviews"]] == [review_id]

    stable_url = f"/api/editarr-items/{title_id}"
    direct = catalog.send("GET", stable_url, owner)
    assert direct.status_code == 200, direct.text
    assert direct.json()["title_id"] == title_id
    current = catalog.send(
        "POST",
        f"{stable_url}/versions",
        catalog.accounts.unsafe_cookie(OWNER),
        {"item_id": _REPLACEMENT_ID},
    )
    assert current.status_code == 200, current.text
    assert current.json()["version_id"] != original_version_id

    restricted = catalog.accounts.cookie(USER)
    unknown = catalog.send("GET", f"/api/editarr-items/{guid('unknown')}", restricted)
    assert unknown.status_code == 404
    for url in (old_url, stable_url):
        response = catalog.send("GET", url, restricted)
        assert response.status_code == unknown.status_code
        assert response.content == unknown.content


def test_modified_middle_in_the_same_jellyfin_slot_blocks_previous_review_publication(catalog):
    scan_id, _ = catalog.seed_scan(OWNER, "Same slot", status="complete")
    review_id = catalog.seed_review(OWNER, "Same slot review", scan_id)
    with catalog.accounts.factory() as db:
        scan = db.get(ScanRow, scan_id)
        path = Path(scan.source_path)
        with path.open("r+b") as media:
            media.truncate(51 * 1024 * 1024)
        media_key = scan.media_item.jellyfin_id
        fingerprint = repository.compute_video_md5(str(path))
        scan.video_hash = fingerprint
        title_id = media_identity_store.observe_title(db, scan.media_item)
        old_digest = sha256(path.read_bytes()).hexdigest()
        remember_content_digest(path, file_generation(str(path)), old_digest)
        original = media_identity_store.observe_version(
            db,
            media=scan.media_item,
            media_source_id=None,
            source_path=scan.source_path,
            resolved_path=scan.resolved_local_path,
            content_sha256=old_digest,
        )
        media_identity_store.bind_scan_version(db, scan_id, original)
        media_identity_store.bind_review_version(db, review_id, original)
        db.commit()
        assert _review_export_scans(db, db.get(SavedReviewRow, review_id))[2] == path

    catalog.jellyfin_item(media_key, LIBRARY_A)
    catalog.items[media_key] = catalog.items[media_key].model_copy(update={"path": str(path)})
    with path.open("r+b") as media:
        media.seek(25 * 1024 * 1024)
        media.write(b"x")
    assert repository.compute_video_md5(str(path)) == fingerprint
    url = f"/api/editarr-items/{title_id}"
    captured = catalog.send(
        "POST", f"{url}/versions", catalog.accounts.unsafe_cookie(OWNER), {"item_id": media_key}
    )
    assert captured.status_code == 200, captured.text
    current = captured.json()["version_id"]
    assert current != original
    result = catalog.send("GET", url, catalog.accounts.cookie(OWNER))
    assert result.status_code == 200, result.text
    assert result.json()["current_versions"] == [
        {"item_id": media_key, "media_source_id": None, "version_id": current}
    ]
    versions = {row["id"]: row for row in result.json()["versions"]}
    assert versions[current]["available"] is True
    assert versions[original]["available"] is False
    assert [row["id"] for row in versions[original]["reviews"]] == [review_id]
    with (
        catalog.accounts.factory() as db,
        pytest.raises(repository.ReviewMembershipError, match="target physical media version"),
    ):
        _review_export_scans(db, db.get(SavedReviewRow, review_id))


def test_sampled_stable_bookmark_recognizes_a_renamed_file_without_full_hash(catalog, monkeypatch):
    from scanner.runtime import media_fingerprints

    def unexpected_hash(*args, **kwargs):
        pytest.fail("Catalog sampled identity started a full media digest")

    monkeypatch.setattr(media_fingerprints, "content_digest", unexpected_hash)
    item_id = catalog.jellyfin_item(guid("sampled-move"), LIBRARY_A)
    item = catalog.items[item_id]
    owner = catalog.accounts.cookie(OWNER)
    resolved = catalog.send("GET", f"/api/editarr-items/legacy/{item_id}", owner)
    assert resolved.status_code == 200, resolved.text
    path = f"/api/editarr-items/{resolved.json()['title_id']}"
    captured = catalog.send(
        "POST", f"{path}/versions", catalog.accounts.unsafe_cookie(OWNER), {"item_id": item_id}
    )
    assert captured.status_code == 200, captured.text
    version_id = captured.json()["version_id"]
    old_path = Path(item.path)
    moved = old_path.rename(old_path.with_suffix(".renamed.mkv"))
    catalog.items[item_id] = item.model_copy(update={"path": str(moved)})
    with api.LIBRARY_ITEMS_CACHE_LOCK:
        api.JELLYFIN_ITEM_CACHE.clear()
    response = catalog.send("GET", f"{path}?version_id={version_id}", owner)
    assert response.status_code == 200, response.text
    assert response.json()["current_versions"] == [{
        "item_id": item_id, "media_source_id": None, "version_id": version_id,
    }]
    assert response.json()["versions"][0]["available"] is True


def test_item_listing_returns_explicit_links_after_association(catalog):
    from scanner.http import catalog_candidates_api, scan_catalog_api

    catalog.client.app.include_router(catalog_candidates_api.router)
    catalog.client.app.include_router(scan_catalog_api.router)
    original_scan, _ = catalog.seed_scan(OWNER, "Original file", status="complete")
    catalog.jellyfin_item(_REPLACEMENT_ID, LIBRARY_A)
    with catalog.accounts.factory() as db:
        target_item = catalog.items[_REPLACEMENT_ID]
        media = repository.upsert_media_item(
            db,
            server_id=api.get_jellyfin_credentials(db)[0],
            jellyfin_id=_REPLACEMENT_ID,
            library_id=LIBRARY_A,
            item_type="Movie",
            name=target_item.name,
            path=target_item.path,
        )
        media_id = media.id

    url = f"/api/library-items/{_REPLACEMENT_ID}/scans?associated_only=true"
    owner = catalog.accounts.cookie(OWNER)
    assert catalog.send("GET", url, owner).json() == []
    response = catalog.send(
        "POST",
        "/api/filter-candidates/associations",
        catalog.accounts.unsafe_cookie(OWNER),
        {"media_item_id": media_id, "scan_ids": [original_scan]},
    )
    assert response.status_code == 204, response.text

    response = catalog.send("GET", url, owner)
    assert response.status_code == 200, response.text
    assert [(row["id"], row["jellyfin_item_id"]) for row in response.json()] == [
        (original_scan, guid("Original file"))
    ]
    assert catalog.send("GET", url, catalog.accounts.cookie(USER)).status_code == 404
