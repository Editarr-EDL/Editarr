"""Users can edit and preview an aligned community set before it belongs to a review."""

import pytest

from scanner import repository
from scanner.db import CommunityMasterLinkRow, ScanRow
from scanner.http import catalog_targets, review_edit_api, review_target_routes
from scanner.modeling.model_reviews import ReviewSave
from scanner.models import JellyfinLibrary, JellyfinMediaItem, JellyfinSettingsUpdate
from scanner.persistence.repository.media_identity_store import observe_sampled_version
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.community_master_http_support import activities, masters
from tests.multi_user_access_support import USER
from tests.scan_access_http import JELLYFIN, _Jellyfin
from tests.test_review_community_sources import FILTERS, community, review, session

__all__ = ["activities", "masters", "review"]


@pytest.fixture
def review_routes(masters, monkeypatch):
    import scanner.api as api

    catalog_targets.jellyfin_libraries.clear()
    with session(masters) as db:
        repository.update_jellyfin_settings(
            db, JellyfinSettingsUpdate(server_url=JELLYFIN, api_key="test-key")
        )
    fake = _Jellyfin(masters.scans.items, masters.scans.calls)
    monkeypatch.setattr(api, "JellyfinClient", fake)
    masters.scans.items[masters.item_id] = JellyfinMediaItem(
        id=masters.item_id, library_id=masters.library_id, name="Movie", item_type="Movie",
        path=masters.path, duration_seconds=120,
    )
    monkeypatch.setattr(
        api.JellyfinClient, "libraries",
        lambda: [JellyfinLibrary(
            id=masters.library_id, name="Movies", locations=[str(masters.scans.media)],
        )],
        raising=False,
    )
    masters.app.client.app.include_router(review_target_routes.router)
    masters.app.client.app.include_router(review_edit_api.router)
    yield
    catalog_targets.jellyfin_libraries.clear()


def draft(masters, *, fingerprint=False):
    link = masters.link(USER, "v_1", "2", *FILTERS)["linkId"]
    if fingerprint:
        with session(masters) as db:
            media = db.get(ScanRow, masters.scan_id).media_item
            version = observe_sampled_version(
                db, media=media, media_source_id=None, source_path=masters.path,
                fingerprint=sampled_media_fingerprint(masters.path),
            )
            db.get(CommunityMasterLinkRow, link).media_version_id = version
            db.commit()
    data = masters.ok("GET", f"/masters/{link}?review_draft=true", USER)
    return link, data


def request_body(masters, review, link, data):
    with session(masters) as db:
        stored = repository.get_saved_review(db, review)
    return ReviewSave(
        revision=stored.revision,
        name=stored.name,
        memberships=stored.memberships,
        filters=stored.filters,
        mcf_document=stored.mcf_document,
        synchronization=stored.synchronization,
        policy_owner_id=stored.policy_owner_id,
        policy_revision=stored.policy_revision,
        community_memberships=[{"link_id": link}],
        community_drafts=[{"link_id": link, "token": data["token"], "filters": data["filters"]}],
    )


def test_first_save_keeps_edited_disabled_and_deleted_filters(masters, review):
    link, data = draft(masters)
    edited = data["filters"][0]
    edited.update(start_seconds=11.5, selected=False, community_edited=True)
    data["filters"] = [edited]
    with session(masters) as db:
        saved = repository.save_saved_review(db, review, request_body(masters, review, link, data))
    assert set(community(saved)) == {"f_a"}
    row = community(saved)["f_a"]
    assert (row.id, row.start_seconds, row.selected) == (edited["id"], 11.5, False)
    assert row.community_source_id == saved.community_sources[0].source_id != link
    assert row.community_edited


def test_new_review_can_save_without_reinstating_deleted_draft_filters(masters, review_routes):
    link, data = draft(masters, fingerprint=True)
    response = masters.app.send(
        "POST",
        f"/api/library-items/{masters.target['item_id']}/reviews",
        USER,
        {
            "name": "New community review",
            "media_source_id": masters.target["media_source_id"],
            "memberships": [],
            "filters": [],
            "community_memberships": [{"link_id": link}],
            "community_drafts": [{"link_id": link, "token": data["token"], "filters": []}],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["filters"] == []
    assert len(response.json()["community_sources"]) == 1


@pytest.mark.parametrize("tampering", ["token", "filter", "duplicate"])
def test_save_rejects_a_stale_or_invalid_draft(masters, review, tampering):
    link, data = draft(masters)
    if tampering == "token":
        data["token"] = "0" * 64
    elif tampering == "filter":
        data["filters"][0]["community_filter_id"] = "f_forged"
    else:
        data["filters"].append(data["filters"][0])
    with session(masters) as db:
        with pytest.raises(repository.ReviewMembershipError):
            repository.save_saved_review(db, review, request_body(masters, review, link, data))
        saved = repository.get_saved_review(db, review)
    assert not saved.community_sources


@pytest.mark.parametrize("saved", [True, False])
def test_preview_checks_cue_permissions(masters, review, review_routes, monkeypatch, saved):
    link, _ = draft(masters, fingerprint=True)
    rendered = []
    monkeypatch.setattr(
        repository, "render_video_preview",
        lambda path, start, end: rendered.append((start, end)) or b"video",
    )
    route = f"/api/library-items/{masters.target['item_id']}/reviews/preview"
    body = {
        "review_id": review if saved else None,
        "media_source_id": masters.target["media_source_id"],
        "community_link_id": link,
        "community_filter_id": "f_a",
        "source": "visual",
        "edit_action": "skip",
        "start_seconds": 10,
        "end_seconds": 20,
    }
    changed = masters.app.send("POST", route, USER, {**body, "start_seconds": 12})
    assert changed.status_code == 200, changed.text
    masters.app.accounts.set_override(USER, C.REVIEWS_CUES_EDIT_OWN, OverrideEffect.DENY)
    unchanged = masters.app.send("POST", route, USER, body)
    assert unchanged.status_code == 200, unchanged.text
    denied = masters.app.send("POST", route, USER, {**body, "start_seconds": 12})
    assert denied.status_code == 403, denied.text
    assert rendered == [(12, 20), (10, 20)]
