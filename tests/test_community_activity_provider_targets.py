"""Provider lookup hides version and metadata state until media access is established."""

import hashlib
from dataclasses import asdict
from types import SimpleNamespace

from scanner.db import CommunityImportRow, CommunityOperationRow, ScanRow
from scanner.http import community_downloads_api
from scanner.integrations.community import lookup_targets
from scanner.integrations.jellyfin import JellyfinError, JellyfinNotFound
from scanner.modeling.media_identity import MediaIdentity
from scanner.models import JellyfinSettingsUpdate
from scanner.repository import update_jellyfin_settings
from tests import test_community_activity_http as activity_http
from tests.multi_user_access_support import OWNER, PEER_USER, USER
from tests.scan_access_http import JELLYFIN, LIBRARY_A, LIBRARY_B, guid
from tests.test_community_activity_access import activities as activities
from tests.test_community_activity_feedback import bundle
from tests.test_community_activity_feedback import feedback as feedback
from tests.test_community_activity_feedback import feedback_service as feedback_service
from tests.test_community_activity_http import community as community
from tests.test_community_identity_users import _enroll
from tests.test_community_verified_import import BOUNDS
from tests.test_stream_fingerprints import generated_streams as generated_streams


def test_provider_lookup_authorizes_before_disclosing_target_state(
    community, activities, monkeypatch
):
    app, service, _homes, scan, _review = community
    _enroll(activities.accounts, service, OWNER, "d" * 64)
    hidden_scan, _ = activities.seed_scan(
        USER, "hidden-provider", library=LIBRARY_B, status="complete"
    )
    activities.limit_to_library(PEER_USER, LIBRARY_A)
    with app.accounts.factory() as db:
        update_jellyfin_settings(db, JellyfinSettingsUpdate(server_url=JELLYFIN, api_key="key-1"))
        hidden_path = db.get(ScanRow, hidden_scan).resolved_local_path
        visible_path = db.get(ScanRow, scan).resolved_local_path
    paths = {"hidden": hidden_path, "visible": visible_path}
    labels = [
        "hidden-ordinary",
        "hidden-metadata",
        "hidden-multiple",
        "visible-ordinary",
        "visible-metadata",
        "visible-multiple",
        "absent",
        "outage",
    ]
    identifiers = {label: guid(label) for label in labels}

    class Provider:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def item(self, identifier):
            identifier = next(label for label, value in identifiers.items() if value == identifier)
            if identifier == "absent":
                raise JellyfinNotFound("Synthetic absence")
            if identifier == "outage":
                raise JellyfinError("Synthetic outage")
            scope, variant = identifier.split("-")
            versions = (
                [
                    SimpleNamespace(
                        id=version_id,
                        path=paths[scope],
                        media_identity=MediaIdentity("movie", ()),
                    )
                    for version_id in ("first", "second")
                ]
                if variant == "multiple"
                else []
            )
            return SimpleNamespace(
                path=paths[scope],
                library_id=LIBRARY_B if scope == "hidden" else LIBRARY_A,
                media_identity=None if variant == "metadata" else MediaIdentity("movie", ()),
                versions=versions,
                media_source_id="first",
            )

    monkeypatch.setattr(lookup_targets, "JellyfinClient", Provider)

    def request(identifier, caller=PEER_USER, **extra):
        return app.send(
            "POST",
            "/api/community/lookup",
            caller,
            {"target": {"source": "jellyfin", "item_id": identifiers[identifier], **extra}},
        )

    hidden = [
        request("hidden-ordinary"),
        request("absent"),
        request("hidden-metadata"),
        request("hidden-ordinary", media_source_id="missing"),
        request("hidden-multiple"),
    ]
    assert [(result.status_code, result.json()) for result in hidden] == [
        (404, {"code": "not_found", "detail": "Not found"})
    ] * len(hidden)
    assert service.posts == []
    visible = request("visible-metadata")
    assert visible.status_code == 409 and visible.json()["code"] == "unsupported_target", (
        visible.text
    )
    changed = request("visible-ordinary", media_source_id="missing")
    assert changed.status_code == 409 and changed.json()["code"] == "target_changed", changed.text
    for caller in (PEER_USER, OWNER):
        result = request("visible-multiple", caller)
        assert result.status_code == 409 and result.json()["code"] == "version_required", (
            result.text
        )
    owner = request("absent", OWNER)
    assert owner.status_code == 409 and owner.json()["code"] == "invalid_target", owner.text
    outage = request("outage")
    assert outage.status_code == 503 and outage.json()["code"] == "target_unavailable", outage.text


def configure_downloads(app, monkeypatch):
    configuration = activity_http.community_activity_access.settings.model_copy(
        update={
            "community_download_bounds": asdict(BOUNDS),
            "community_download_response_bytes": 1_000_000,
        }
    )
    monkeypatch.setattr(community_downloads_api, "settings", configuration)


def download(app, caller, target):
    value, _keys = bundle("reader")
    return app.send(
        "POST",
        "/api/community/downloads",
        caller,
        {
            "lookup": {"target": target, "target_key": hashlib.sha256(b"k").hexdigest()},
            "choice": {
                "contributionId": "contribution-root",
                "revisionId": value["revisionId"],
                "artifactDigest": value["artifactDigest"],
                "channels": ["video"],
            },
            "confirmed": True,
        },
    )


def counts(app):
    with app.accounts.factory() as db:
        return (
            db.query(ScanRow).count(),
            db.query(CommunityImportRow).count(),
            db.query(CommunityOperationRow).count(),
        )


def test_provider_download_hidden_matches_confirmed_absence(feedback, activities, monkeypatch):
    app, service, _homes, _scan, _review = feedback
    configure_downloads(app, monkeypatch)
    _enroll(activities.accounts, service, OWNER, "d" * 64)
    hidden_scan, _ = activities.seed_scan(USER, "hidden-dl", library=LIBRARY_B, status="complete")
    activities.limit_to_library(PEER_USER, LIBRARY_A)
    with app.accounts.factory() as db:
        update_jellyfin_settings(db, JellyfinSettingsUpdate(server_url=JELLYFIN, api_key="key-1"))
        stored = db.get(ScanRow, hidden_scan)
        item_id, path = stored.media_item.jellyfin_id, stored.resolved_local_path
    absent_id = "f" * 32
    item = SimpleNamespace(
        path=path,
        library_id=LIBRARY_B,
        media_identity=MediaIdentity("movie", ()),
        versions=[],
        media_source_id="first",
    )

    class Provider:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def item(self, identifier):
            if identifier != item_id:
                raise JellyfinNotFound("Synthetic absent item")
            return item

    monkeypatch.setattr(lookup_targets, "JellyfinClient", Provider)
    before = counts(app)
    observed = [
        (result.status_code, result.json())
        for result in (
            download(app, PEER_USER, {"source": "jellyfin", "item_id": identifier})
            for identifier in (item_id, absent_id)
        )
    ]
    assert counts(app) == before and service.feedback == [] and service.posts == []
    assert observed == [(404, {"code": "not_found", "detail": "Not found"})] * 2
    owner = download(app, OWNER, {"source": "jellyfin", "item_id": absent_id})
    assert owner.status_code == 409 and owner.json()["code"] == "invalid_target", owner.text
    assert counts(app) == before and service.feedback == [] and service.posts == []
