"""MU-17 contracts beyond the route matrix: bodies, bytes, passwords, and alternate paths."""

from __future__ import annotations

import pytest

import scanner.api as root
from scanner.http import backup_api, setup_restore_api
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import (
    ADMIN,
    OWNER,
    USER,
    live_accounts,
)
from tests.operations_access_http import ARCHIVE, BACKEND, SECRETS, serve_operations


@pytest.fixture
def start(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    apps = []

    def launch():
        app = serve_operations(monkeypatch, accounts, tmp_path)
        apps.append(app)
        return app

    yield launch
    for app in apps:
        app.client.close()
    backup_api._jobs.clear()
    accounts.close()


def _join_prefetch() -> None:
    for task in list(root._model_tasks().values()):
        task.join(5)


@pytest.mark.parametrize("credential", ["cookie", "key"])
def test_a_user_starts_a_model_download_without_seeing_the_inventory(start, credential):
    app = start()
    user = getattr(app.accounts, "unsafe_cookie" if credential == "cookie" else credential)(USER)
    admin = getattr(app.accounts, "unsafe_cookie" if credential == "cookie" else credential)(ADMIN)

    inventory = app.send("GET", "/api/models", user)
    started = app.send("POST", "/api/models/prefetch", user, {"backend_id": BACKEND})
    _join_prefetch()
    cancel = app.send("POST", "/api/models/faster-whisper-base/cancel", user)

    assert (inventory.status_code, inventory.json()["code"]) == (403, "capability_denied")
    assert "artifacts" not in inventory.text
    assert started.status_code == 202
    assert started.json() == {"status": None, "accepted": True, "removed": False}
    assert f"prefetch:{BACKEND}" in app.calls
    assert (cancel.status_code, cancel.json()["code"]) == (403, "capability_denied")
    viewed = app.send("POST", "/api/models/prefetch", admin, {"backend_id": BACKEND})
    _join_prefetch()
    assert [item["artifact_id"] for item in viewed.json()["status"]["artifacts"]] == [
        "faster-whisper-base"
    ]


def test_no_signed_in_account_can_restore_a_backup(start):
    app = start()
    job_id = app.seed_job("restore", "ready")
    for suffix, method in (("archive", "PUT"), ("preflight", "POST"), ("activate", "POST")):
        for headers in (app.accounts.unsafe_cookie(OWNER), app.accounts.key(OWNER)):
            response = app.send(method, f"/api/backups/jobs/{job_id}/{suffix}", headers, {})
            assert response.status_code in (404, 405), (suffix, response.status_code)
    refused = app.send(
        "POST", "/api/backups/jobs", app.accounts.unsafe_cookie(OWNER), {"action": "restore"}
    )
    assert refused.status_code == 422
    assert backup_api._jobs[job_id].state == "ready"
    assert [event for event in app.audit() if event[0] == "backup.restore"] == []


def test_only_the_owner_receives_backup_bytes(start):
    app = start()
    job_id = app.seed_job("export", "ready")
    path = f"/api/backups/jobs/{job_id}/download"

    for headers in (app.accounts.unsafe_cookie(ADMIN), app.accounts.key(ADMIN)):
        for method in ("GET", "HEAD"):
            refused = app.send(method, path, headers)
            assert refused.status_code == 403
            assert ARCHIVE not in refused.content
    status = app.send("GET", f"/api/backups/jobs/{job_id}", app.accounts.key(ADMIN))
    downloaded = app.send("GET", path, app.accounts.key(OWNER))

    assert status.status_code == 200 and ARCHIVE not in status.content
    assert downloaded.content == ARCHIVE
    assert downloaded.headers["cache-control"] == "no-store"
    assert app.audit()[-1] == (
        "backup.download",
        "succeeded",
        OWNER,
        "api_key",
        job_id,
        {"kind": "full"},
    )


def test_admins_see_connection_status_without_keys_and_cannot_be_granted_changes(start):
    app = start()
    jellyfin = app.send("GET", "/api/settings/jellyfin", app.accounts.key(ADMIN)).json()

    assert jellyfin["server_url"] == "http://jellyfin.test" and jellyfin["api_key_configured"]
    assert not any(secret in str(jellyfin) for secret in SECRETS)
    for capability in (C.CONNECTIONS_EDIT, C.DEPLOYMENT_EDIT, C.BACKUPS_DOWNLOAD):
        with pytest.raises(ValueError):
            app.accounts.set_override(ADMIN, capability, OverrideEffect.ALLOW)


def test_an_owner_may_delegate_mapping_edits_but_not_connections(start):
    app = start()
    body = {"remote_path": "/remote/b", "local_path": "/local/b"}
    assert (
        app.send("POST", "/api/settings/path-mappings", app.accounts.key(ADMIN), body).status_code
        == 403
    )
    app.accounts.set_override(ADMIN, C.PATH_MAPPINGS_CREATE, OverrideEffect.ALLOW)
    created = app.send("POST", "/api/settings/path-mappings", app.accounts.key(ADMIN), body)
    assert created.status_code == 200 and created.json()["local_path"] == "/local/b"


@pytest.mark.parametrize("credential", ["unsafe_cookie", "key"])
def test_setup_restore_is_closed_once_an_owner_exists(start, credential):
    app = start()
    app.client.app.include_router(setup_restore_api.router)
    headers = getattr(app.accounts, credential)(ADMIN)
    response = app.send("POST", "/api/setup-restore/jobs", headers, {})
    assert (response.status_code, response.json()["code"]) == (409, "setup_already_complete")
    assert backup_api._jobs == {}
