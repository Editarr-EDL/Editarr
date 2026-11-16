"""Catalog-bound local downloads use real frozen signed artifacts and temporary SQLite."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from scanner.db import CommunityImportRow, MediaItemRow
from scanner.integrations.community import local_downloads as module
from scanner.integrations.community.lookup_targets import CapturedTarget
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_matching import POLICY_VERSION, classify
from scanner.modeling.model_community_downloads import DownloadRequest, DownloadStatusRequest
from scanner.modeling.model_community_lookup import LookupSummary
from scanner.pipeline.audio_tracks import _file_state
from scanner.security.private_identity_files import IdentityHome
from tests.community_identity_support import only_identity_ref
from tests.test_community_api import context as context
from tests.test_community_api import headers
from tests.test_community_verified_import import BOUNDS, SERVICE, local_target, verified_input
from tests.test_community_verified_import import import_database as import_database


@pytest.fixture
def scenario(import_database, tmp_path, monkeypatch):
    downloaded = verified_input("retimed")
    target = local_target(downloaded, tmp_path)
    path = Path(target.resolved_local_path)
    path.write_bytes(b"synthetic temporary media")
    captured = CapturedTarget(path, _file_state(path), target.evidence, "a" * 64, ())
    monkeypatch.setattr(module, "capture_target", lambda *_args, **_kwargs: captured)
    monkeypatch.setattr(
        module, "media_row", lambda db, _target: db.get(MediaItemRow, target.media_item_id)
    )
    identity = SimpleNamespace(
        contributor_id="reader", key_id="reader-key", pending_replacement=None
    )
    summary = downloaded.verified.artifact.summary
    candidate = {
        "contributionId": downloaded.contribution_id,
        "revisionId": downloaded.revision_id,
        "artifactDigest": downloaded.verified.manifest.manifest.artifact_digest,
        "artifactSchema": "community-artifact-2",
        "releaseId": "different-release-id",
        "catalogCreatedAt": 1,
        "attribution": {"contributorId": "author", "displayName": "Public Author"},
        "summary": {
            key: value for key, value in summary.items() if key in LookupSummary.model_fields
        },
        "evidence": asdict(target.evidence),
        "compatibility": asdict(classify(target.evidence, target.evidence)),
        "statistics": {"imports": 0, "likes": 0, "dislikes": 0},
        "ranking": None,
    }
    sent = []

    async def request(_method, _path, **kwargs):
        sent.append(json.loads(kwargs["body"]))
        return {
            "version": 1,
            "policyVersion": POLICY_VERSION,
            "candidates": [candidate],
            "nextCursor": None,
        }

    async def fetch(*_args, **_kwargs):
        return downloaded

    monkeypatch.setattr(module, "fetch_verified_download", fetch)
    choice = {key: candidate[key] for key in ("contributionId", "revisionId", "artifactDigest")}
    choice["channels"] = ["video"]
    body = DownloadRequest.model_validate(
        {
            "lookup": {
                "target": {"source": "jellyfin", "item_id": "media-legacy"},
                "target_key": captured.key,
            },
            "choice": choice,
            "confirmed": True,
        }
    )
    with Session(import_database) as db:
        client = SimpleNamespace(
            db=db,
            home=IdentityHome(Path(import_database.url.database).parent, only_identity_ref(db)),
            transport=SimpleNamespace(request=request),
            _identity=lambda: identity,
        )
        yield SimpleNamespace(
            db=db,
            service=module.CommunityDownloads(client, bounds=BOUNDS, response_bytes=1_000_000),
            body=body,
            captured=captured,
            downloaded=downloaded,
            sent=sent,
        )


def status(scenario):
    return module.download_status(
        scenario.db,
        SERVICE,
        DownloadStatusRequest(
            target=scenario.body.lookup.target,
            target_key=scenario.captured.key,
            choices=[scenario.body.choice],
        ),
    )


def test_catalog_bound_import_reuses_and_reconciles_original_receipt(scenario):
    first = asyncio.run(scenario.service.run(scenario.body))
    assert first["status"] == "imported" and first["receiptState"] == "pending"
    assert status(scenario)["outcomes"][0]["status"] == "imported"
    second = asyncio.run(scenario.service.run(scenario.body))
    assert second["status"] == "reused" and second["scan"]["id"] == first["scan"]["id"]
    assert scenario.db.query(CommunityImportRow).count() == 1
    assert "local-only.mp4" not in json.dumps(scenario.sent)


@pytest.mark.parametrize("change", ["root", "digest", "target"])
def test_browser_assertions_cannot_override_authenticated_identity(scenario, change):
    body = scenario.body.model_copy(deep=True)
    if change == "root":
        body.choice.contributionId = "different-release-id"
    elif change == "digest":
        body.choice.artifactDigest = "b" * 64
    else:
        body.lookup.target_key = "c" * 64
    with pytest.raises(CommunityFailure, match="candidate_changed|target_changed"):
        asyncio.run(scenario.service.run(body))
    assert scenario.db.query(CommunityImportRow).count() == 0


def test_uncertain_channel_needs_deliberate_confirmation(scenario):
    body = scenario.body.model_copy(update={"confirmed": False})
    with pytest.raises(CommunityFailure, match="explicit_review_required"):
        asyncio.run(scenario.service.run(body))
    assert status(scenario)["outcomes"][0]["status"] == "not_imported"


def test_cancel_before_commit_never_claims_imported(scenario, monkeypatch):
    cancelled = Event()

    async def fetch(*_args, **_kwargs):
        cancelled.set()
        return scenario.downloaded

    monkeypatch.setattr(module, "fetch_verified_download", fetch)
    with pytest.raises(CommunityFailure, match="lookup_cancelled"):
        asyncio.run(scenario.service.run(scenario.body, should_cancel=cancelled.is_set))
    assert status(scenario)["outcomes"][0]["status"] == "not_imported"


def test_lost_response_after_commit_is_reconciled_without_duplicate(scenario, monkeypatch):
    original = module.import_verified_download

    def lost_response(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("lost response")

    monkeypatch.setattr(module, "import_verified_download", lost_response)
    with pytest.raises(RuntimeError, match="lost response"):
        asyncio.run(scenario.service.run(scenario.body))
    assert status(scenario)["outcomes"][0]["status"] == "imported"
    monkeypatch.setattr(module, "import_verified_download", original)
    assert asyncio.run(scenario.service.run(scenario.body))["status"] == "reused"


def test_download_routes_require_real_session_origin_csrf_and_explicit_limits(context, monkeypatch):
    from scanner.http import community_downloads_api as api

    client, issued = context
    choice = {
        "contributionId": "root",
        "revisionId": "revision",
        "artifactDigest": "a" * 64,
        "channels": ["video"],
    }
    target = {"source": "jellyfin", "item_id": "movie"}
    body = {"lookup": {"target": target, "target_key": "a" * 64}, "choice": choice}
    status_body = {"target": target, "target_key": "a" * 64, "choices": [choice]}
    monkeypatch.setattr(api.settings, "community_download_bounds", None)
    for suffix, value in (("", body), ("/status", status_body)):
        path = "/editarr/api/community/downloads" + suffix
        assert client.post(path, json=value).status_code == 401
        missing = {key: value for key, value in headers(issued).items() if key != "X-CSRF-Token"}
        assert client.post(path, json=value, headers=missing).status_code == 403
        assert (
            client.post(
                path, json=value, headers=headers(issued) | {"Origin": "https://attacker.test"}
            ).status_code
            == 403
        )
    response = client.post("/editarr/api/community/downloads", json=body, headers=headers(issued))
    assert response.json()["code"] == "downloads_not_configured"
    assert response.headers["Cache-Control"] == "no-store"
    assert (
        client.post(
            "/editarr/api/community/downloads",
            json=body | {"confirmed": 0},
            headers=headers(issued),
        ).status_code
        == 422
    )
    monkeypatch.setattr(
        api,
        "download_status",
        lambda *_args, **_kwargs: {
            "version": 1,
            "outcomes": [{"revisionId": "revision", "status": "not_imported"}],
        },
    )
    monkeypatch.setattr(api, "authorize_status", lambda *_args: None)
    response = client.post(
        "/editarr/api/community/downloads/status", json=status_body, headers=headers(issued)
    )
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"


def test_cancellation_drains_running_worker_before_propagating():
    async def run():
        started = asyncio.Event()
        release = Event()
        finished = Event()
        loop = asyncio.get_running_loop()

        def worker():
            loop.call_soon_threadsafe(started.set)
            assert release.wait(timeout=2)
            finished.set()

        task = asyncio.create_task(module.drained_thread(worker))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())
