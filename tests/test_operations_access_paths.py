"""MU-17 fix round: path visibility, authorization before body validation, and schedule roots."""

from __future__ import annotations

import pytest

from scanner.backup.schedule_store import STATE_FILE
from scanner.http import api_system, backup_api
from scanner.runtime.production_frontend import FrontendAssets
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect as Effect
from tests.multi_user_access_support import ADMIN, OWNER, USER, live_accounts
from tests.operations_access_http import serve_operations
from tests.test_production_frontend import _write_frontend

KINDS = ["unsafe_cookie", "key"]
LOGGED_PATH = "/srv/private-media/Restricted/Private.mkv"


@pytest.fixture
def app(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    running = serve_operations(monkeypatch, accounts, tmp_path)
    yield running
    running.client.close()
    backup_api._jobs.clear()
    accounts.close()


def _headers(app, user_id, kind):
    return getattr(app.accounts, kind)(user_id)


def _code(response):
    body = response.json()
    return response.status_code, body.get("code") if isinstance(body, dict) else None


def _deny_paths(app, user_id=ADMIN):
    app.accounts.set_override(user_id, C.MEDIA_PATHS_VIEW, Effect.DENY)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    ("method", "path", "body", "marker"),
    [
        ("GET", "/api/system/logs/application", None, LOGGED_PATH),
        ("GET", "/api/settings/path-mappings", None, "/local/a"),
        (
            "POST",
            "/api/settings/path-mappings/resolve",
            {"remote_path": "/remote/a/Private.mkv"},
            "/local/a",
        ),
    ],
)
def test_path_reads_are_refused_without_the_live_path_grant(app, kind, method, path, body, marker):
    (api_system.settings.log_dir / "application.log").write_text(f"INFO Scanning {LOGGED_PATH}\n")
    headers = _headers(app, ADMIN, kind)

    allowed = app.send(method, path, headers, body)
    assert allowed.status_code == 200
    assert marker in allowed.text

    _deny_paths(app)
    refused = app.send(method, path, headers, body)
    assert _code(refused) == (403, "capability_denied")
    assert marker not in refused.text

    owner = app.send(method, path, _headers(app, OWNER, kind), body)
    assert marker in owner.text


@pytest.mark.parametrize("kind", KINDS)
def test_schedule_status_hides_server_paths_without_the_path_grant(app, kind):
    body = app.schedule_body()
    destination = body["destination"]
    admin = _headers(app, ADMIN, kind)
    assert (
        app.send("PUT", "/api/backups/schedule", _headers(app, OWNER, kind), body).status_code
        == 200
    )
    assert app.send("GET", "/api/backups/schedule", admin).json()["config"]["destination"] == (
        destination
    )

    _deny_paths(app)
    for method, request in (("GET", None), ("PUT", body), ("DELETE", None)):
        response = app.send(method, "/api/backups/schedule", admin, request)
        assert response.status_code == 200
        assert destination not in response.text
        config = response.json()["config"]
        assert (config["destination"], config["passphrase_file"]) == (None, None)
        assert config["interval_hours"] == body["interval_hours"]
    owner = app.send("GET", "/api/backups/schedule", _headers(app, OWNER, kind))
    assert owner.json()["config"]["destination"] == destination


@pytest.mark.parametrize("kind", KINDS)
def test_delegated_mapping_edits_also_need_the_path_grant(app, kind):
    for capability in (C.PATH_MAPPINGS_CREATE, C.PATH_MAPPINGS_UPDATE, C.PATH_MAPPINGS_REORDER):
        app.accounts.set_override(ADMIN, capability, Effect.ALLOW)
    _deny_paths(app)
    headers = _headers(app, ADMIN, kind)
    mapping_id = app.send("GET", "/api/settings/path-mappings", _headers(app, OWNER, kind)).json()[
        0
    ]["id"]
    before, audit_before = app.state(), len(app.audit())
    for method, path, body in (
        ("POST", "/api/settings/path-mappings", {"remote_path": "/r/b", "local_path": "/l/b"}),
        ("PATCH", f"/api/settings/path-mappings/{mapping_id}", {"local_path": "/l/c"}),
        ("POST", "/api/settings/path-mappings/reorder", {"mapping_ids": [mapping_id]}),
    ):
        response = app.send(method, path, headers, body)
        assert _code(response) == (403, "capability_denied")
        assert "/local/a" not in response.text
    assert app.state() == before
    assert [(event[0], event[1], event[5]) for event in app.audit()[audit_before:]] == [
        (
            "settings.path_mapping.change",
            "denied",
            {"reason": "capability_denied", "change": change},
        )
        for change in ("created", "updated", "reordered")
    ]

    app.accounts.set_override(ADMIN, C.MEDIA_PATHS_VIEW, Effect.INHERIT)
    reordered = app.send(
        "POST", "/api/settings/path-mappings/reorder", headers, {"mapping_ids": [mapping_id]}
    )
    assert reordered.status_code == 200
    assert "/local/a" in reordered.text


@pytest.mark.parametrize("kind", KINDS)
def test_backup_start_refuses_before_validating_the_body(app, kind):
    audit_before = len(app.audit())
    user = _headers(app, USER, kind)
    assert _code(app.send("POST", "/api/backups/jobs", user, {})) == (403, "capability_denied")
    assert _code(app.send("POST", "/api/backups/jobs", user, {"action": "export", "kind": 4})) == (
        403,
        "capability_denied",
    )
    assert [(event[0], event[1]) for event in app.audit()[audit_before:]] == [
        ("backup.create", "denied"),
        ("backup.create", "denied"),
    ]
    admin = _headers(app, ADMIN, kind)
    assert (
        app.send("POST", "/api/backups/jobs", admin, {"action": "export", "kind": 4}).status_code
        == 422
    )
    assert app.send("POST", "/api/backups/jobs", admin, {"action": "restore"}).status_code == 422
    assert not backup_api._jobs


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("inside", ["", "assets"])
def test_schedule_destination_cannot_be_a_served_static_root(app, tmp_path, kind, inside):
    frontend = tmp_path / "frontend"
    _write_frontend(frontend)
    app.client.app.state.frontend_assets = FrontendAssets.from_directory(frontend)
    body = {**app.schedule_body(), "destination": str(frontend / inside)}

    response = app.send("PUT", "/api/backups/schedule", _headers(app, ADMIN, kind), body)

    assert response.status_code == 400
    assert not (app.data / STATE_FILE).exists()
    assert not list(frontend.rglob("editarr-*"))
    allowed = app.send(
        "PUT", "/api/backups/schedule", _headers(app, ADMIN, kind), app.schedule_body()
    )
    assert allowed.status_code == 200
