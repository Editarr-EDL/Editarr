"""Hidden community targets remain indistinguishable from absent resources."""

import hashlib

import pytest

from scanner.db import CommunityOperationRow, ScanRow
from scanner.integrations.community import lookup_targets
from scanner.integrations.jellyfin import JellyfinNotFound
from scanner.models import JellyfinSettingsUpdate
from scanner.repository import update_jellyfin_settings
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, OWNER, PEER_USER, USER
from tests.scan_access_http import JELLYFIN, LIBRARY_A, LIBRARY_B
from tests.test_community_activity_access import activities as activities
from tests.test_community_activity_http import community as community
from tests.test_community_activity_http import preview
from tests.test_stream_fingerprints import generated_streams as generated_streams


@pytest.mark.parametrize("deny_findings", [False, True])
def test_scan_publication_hidden_matches_absent(community, deny_findings):
    app, service, _homes, scan, _review = community
    prepared = preview(app, USER, "scan", scan)
    assert prepared.status_code == 200, prepared.text
    approved = app.send(
        "POST",
        f"/api/community/sharing/previews/{prepared.json()['previewId']}/approve",
        USER,
        {"confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    if deny_findings:
        app.accounts.set_override(PEER_USER, C.SCANS_FINDINGS_READ, OverrideEffect.DENY)
    for identifier in (approved.json()["operationId"], "publish-absent"):
        result = app.send("GET", f"/api/community/publications/{identifier}", PEER_USER)
        assert result.status_code == 404 and result.json()["code"] == "not_found", result.text
    assert (
        app.send(
            "GET", f"/api/community/publications/{approved.json()['operationId']}", ADMIN
        ).status_code
        == 200
    )
    assert service.posts == []


def test_download_status_hidden_matches_absent(community, activities):
    app, service, _homes, _scan, _review = community
    hidden_scan, _ = activities.seed_scan(USER, "library-b", library=LIBRARY_B, status="complete")
    activities.limit_to_library(PEER_USER, LIBRARY_A)
    with app.accounts.factory() as db:
        update_jellyfin_settings(db, JellyfinSettingsUpdate(server_url=JELLYFIN, api_key="key-1"))
        item = db.get(ScanRow, hidden_scan).media_item.jellyfin_id
        before = db.query(CommunityOperationRow).count()
    for identifier in (item, "f" * 32):
        result = app.send(
            "POST",
            "/api/community/downloads/status",
            PEER_USER,
            {
                "target": {"source": "jellyfin", "item_id": identifier},
                "target_key": hashlib.sha256(b"scope").hexdigest(),
                "choices": [],
            },
        )
        assert result.status_code == 404 and result.json()["code"] == "not_found", result.text
    with app.accounts.factory() as db:
        assert db.query(CommunityOperationRow).count() == before
    assert service.posts == []


@pytest.mark.parametrize("caller", [USER, PEER_USER])
def test_lookup_refuses_a_target_outside_jellyfin(community, caller):
    app, service, _homes, _scan, _review = community
    for target in (
        {"source": "manual", "item_id": "manual:/movie.mkv"},
        {"source": "jellyfin", "item_id": "item", "video_path": "/movie.mkv"},
    ):
        result = app.send("POST", "/api/community/lookup", caller, {"target": target})
        assert result.status_code == 422, result.text
    assert service.posts == []


def test_owner_absent_download_status_keeps_diagnostic(community, monkeypatch):
    app, _service, _homes, _scan, _review = community
    with app.accounts.factory() as db:
        update_jellyfin_settings(db, JellyfinSettingsUpdate(server_url=JELLYFIN, api_key="key-1"))

    class Provider:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def item(self, _identifier):
            raise JellyfinNotFound("Synthetic absent item")

    monkeypatch.setattr(lookup_targets, "JellyfinClient", Provider)
    result = app.send(
        "POST",
        "/api/community/downloads/status",
        OWNER,
        {
            "target": {"source": "jellyfin", "item_id": "f" * 32},
            "target_key": hashlib.sha256(b"absent").hexdigest(),
            "choices": [],
        },
    )
    assert result.status_code == 409 and result.json()["code"] == "invalid_target"
