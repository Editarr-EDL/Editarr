"""MU-17: every settings and operations route, for sessions and API keys, over real HTTP.

Each matrix row from `docs/multi-user-permissions.md` "Instance settings and
operations" maps to capabilities in `catalog_operations`. For every route, the
role that holds the capability by default reaches the handler, and the next
role down (or an ordinary user with a deny override, for rows everyone holds)
gets `capability_denied` with nothing changed and nothing disclosed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from scanner.http import backup_api
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.catalog import CAPABILITIES
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.route_table import ROUTE_RULES
from scanner.security.access.vocabulary import Role
from scanner.security.audit.actions import AuditAction as A
from tests.multi_user_access_support import ADMIN, OWNER, PEER_USER, USER, live_accounts
from tests.operations_access_http import ARCHIVE, ARTIFACT, BACKEND, SECRETS, serve_operations


@dataclass(frozen=True)
class Case:
    method: str
    template: str
    capability: C
    status: int
    body: object = None
    job: tuple[str, str] | None = None
    audited: A | None = None
    content: bytes | None = None

    @property
    def id(self) -> str:
        return f"{self.method} {self.template} {self.capability.value}"


_JELLYFIN = {"server_url": "http://jellyfin.test", "api_key": SECRETS[1]}
CASES = (
    Case("GET", "/api/system/version", C.SYSTEM_VERSION_VIEW, 200),
    Case("GET", "/api/system/restart", C.SYSTEM_RESTART, 200),
    Case("POST", "/api/system/restart", C.SYSTEM_RESTART, 202, audited=A.RESTART),
    Case("GET", "/api/system/logs/settings", C.LOGS_VIEW, 200),
    Case(
        "PUT",
        "/api/system/logs/settings",
        C.LOGS_SETTINGS_EDIT,
        200,
        {"debug_enabled": False},
        audited=A.LOGGING_CHANGE,
    ),
    Case("GET", "/api/system/logs/{channel}", C.LOGS_VIEW, 200),
    Case("GET", "/api/settings/deployment", C.DEPLOYMENT_VIEW, 200),
    Case(
        "PUT",
        "/api/settings/deployment",
        C.DEPLOYMENT_EDIT,
        200,
        {"port": 8123},
        audited=A.DEPLOYMENT_CHANGE,
    ),
    Case("GET", "/api/settings/jellyfin", C.CONNECTIONS_VIEW, 200),
    Case(
        "PUT",
        "/api/settings/jellyfin",
        C.CONNECTIONS_EDIT,
        200,
        _JELLYFIN,
        audited=A.CONNECTION_CHANGE,
    ),
    Case(
        "DELETE",
        "/api/settings/jellyfin",
        C.CONNECTIONS_DISCONNECT,
        200,
        audited=A.CONNECTION_CHANGE,
    ),
    Case("POST", "/api/settings/jellyfin/test", C.CONNECTIONS_TEST, 200, _JELLYFIN),
    Case("GET", "/api/settings/path-mappings", C.PATH_MAPPINGS_VIEW, 200),
    Case(
        "POST",
        "/api/settings/path-mappings",
        C.PATH_MAPPINGS_CREATE,
        200,
        {"remote_path": "/remote/b", "local_path": "/local/b"},
        audited=A.PATH_MAPPING_CHANGE,
    ),
    Case(
        "PATCH",
        "/api/settings/path-mappings/{mapping_id}",
        C.PATH_MAPPINGS_UPDATE,
        200,
        {"local_path": "/local/c"},
        audited=A.PATH_MAPPING_CHANGE,
    ),
    Case(
        "DELETE",
        "/api/settings/path-mappings/{mapping_id}",
        C.PATH_MAPPINGS_DELETE,
        200,
        audited=A.PATH_MAPPING_CHANGE,
    ),
    Case(
        "POST",
        "/api/settings/path-mappings/reorder",
        C.PATH_MAPPINGS_REORDER,
        200,
        {"mapping_ids": [1]},
        audited=A.PATH_MAPPING_CHANGE,
    ),
    Case(
        "POST",
        "/api/settings/path-mappings/resolve",
        C.PATH_MAPPINGS_TEST,
        200,
        {"remote_path": "/remote/a/movie.mkv"},
    ),
    Case("GET", "/api/models", C.MODELS_INVENTORY_VIEW, 200),
    Case("POST", "/api/models/prefetch", C.MODELS_DOWNLOAD, 202, {"backend_id": BACKEND}),
    Case("POST", "/api/models/{artifact_id}/cancel", C.MODELS_DOWNLOAD_CANCEL, 200),
    Case("DELETE", "/api/models/{artifact_id}", C.MODELS_DELETE, 200),
    Case(
        "POST",
        "/api/backups/jobs",
        C.BACKUPS_CREATE,
        200,
        {"action": "export"},
        audited=A.BACKUP_CREATE,
    ),
    Case("GET", "/api/backups/jobs/{job_id}", C.BACKUPS_STATUS_VIEW, 200, job=("export", "ready")),
    Case(
        "DELETE",
        "/api/backups/jobs/{job_id}",
        C.BACKUPS_DELETE,
        200,
        job=("export", "ready"),
        audited=A.BACKUP_DELETE,
    ),
    Case(
        "GET",
        "/api/backups/jobs/{job_id}/download",
        C.BACKUPS_DOWNLOAD,
        200,
        job=("export", "ready"),
        audited=A.BACKUP_DOWNLOAD,
    ),
    Case(
        "HEAD",
        "/api/backups/jobs/{job_id}/download",
        C.BACKUPS_DOWNLOAD,
        200,
        job=("export", "ready"),
    ),
    Case("GET", "/api/backups/schedule", C.BACKUPS_STATUS_VIEW, 200),
    Case(
        "PUT",
        "/api/backups/schedule",
        C.BACKUPS_SCHEDULE_EDIT,
        200,
        "schedule",
        audited=A.BACKUP_SCHEDULE_CHANGE,
    ),
    Case(
        "DELETE",
        "/api/backups/schedule",
        C.BACKUPS_SCHEDULE_EDIT,
        200,
        audited=A.BACKUP_SCHEDULE_CHANGE,
    ),
)
_PATH_VALUES = {
    "{channel}": "application",
    "{mapping_id}": "1",
    "{artifact_id}": ARTIFACT,
}


def _holder(capability: C) -> Role:
    granted = CAPABILITIES[capability].granted
    return (
        Role.USER if Role.USER in granted else Role.ADMIN if Role.ADMIN in granted else Role.OWNER
    )


def _account(role: Role):
    return {Role.OWNER: OWNER, Role.ADMIN: ADMIN, Role.USER: USER}[role]


def _headers(accounts, user_id, credential: str) -> dict:
    return accounts.unsafe_cookie(user_id) if credential == "session" else accounts.key(user_id)


@pytest.fixture
def operations(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    app = serve_operations(monkeypatch, accounts, tmp_path)
    yield app
    app.client.close()
    backup_api._jobs.clear()
    accounts.close()


def _prepare(app, case: Case) -> tuple[str, object]:
    path = case.template
    for name, value in _PATH_VALUES.items():
        path = path.replace(name, value)
    if case.job is not None:
        path = path.replace("{job_id}", app.seed_job(*case.job))
    return path, app.schedule_body() if case.body == "schedule" else case.body


OPERATION_ROUTES = frozenset(
    {
        ("DELETE", "/api/backups/jobs/{job_id}"),
        ("DELETE", "/api/backups/schedule"),
        ("DELETE", "/api/models/{artifact_id}"),
        ("DELETE", "/api/settings/jellyfin"),
        ("DELETE", "/api/settings/path-mappings/{mapping_id}"),
        ("GET", "/api/backups/jobs/{job_id}"),
        ("GET", "/api/backups/jobs/{job_id}/download"),
        ("GET", "/api/backups/schedule"),
        ("GET", "/api/models"),
        ("GET", "/api/settings/deployment"),
        ("GET", "/api/settings/jellyfin"),
        ("GET", "/api/settings/path-mappings"),
        ("GET", "/api/system/logs/settings"),
        ("GET", "/api/system/logs/{channel}"),
        ("GET", "/api/system/restart"),
        ("GET", "/api/system/version"),
        ("HEAD", "/api/backups/jobs/{job_id}/download"),
        ("PATCH", "/api/settings/path-mappings/{mapping_id}"),
        ("POST", "/api/backups/jobs"),
        ("POST", "/api/models/prefetch"),
        ("POST", "/api/models/{artifact_id}/cancel"),
        ("POST", "/api/settings/jellyfin/test"),
        ("POST", "/api/settings/path-mappings"),
        ("POST", "/api/settings/path-mappings/reorder"),
        ("POST", "/api/settings/path-mappings/resolve"),
        ("POST", "/api/system/restart"),
        ("PUT", "/api/backups/schedule"),
        ("PUT", "/api/settings/deployment"),
        ("PUT", "/api/settings/jellyfin"),
        ("PUT", "/api/system/logs/settings"),
    }
)


def test_the_matrix_covers_exactly_the_operation_routes_and_their_table_rows():
    covered = {(case.method, case.template) for case in CASES}
    assert covered == OPERATION_ROUTES
    for case in CASES:
        assert case.capability in ROUTE_RULES[(case.method, case.template)].capabilities


@pytest.mark.parametrize("credential", ["session", "api_key"])
@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_each_route_admits_its_default_role_and_refuses_the_next(operations, case, credential):
    app, accounts = operations, operations.accounts
    holder = _holder(case.capability)
    refused_account = {Role.OWNER: ADMIN, Role.ADMIN: USER, Role.USER: PEER_USER}[holder]
    if holder is Role.USER:
        accounts.set_override(PEER_USER, case.capability, OverrideEffect.DENY)
    path, body = _prepare(app, case)
    expected_refusal = "capability_denied"
    before, audit_before = app.state(), app.audit()

    refused = app.send(
        case.method, path, _headers(accounts, refused_account, credential), body, case.content
    )

    assert refused.status_code == 403
    if case.method != "HEAD":
        assert refused.json()["code"] == expected_refusal
    assert app.state() == before
    assert not any(secret in refused.text for secret in SECRETS)
    assert ARCHIVE not in refused.content
    new_events = app.audit()[len(audit_before) :]
    if case.audited is not None:
        credential_kind = "session" if credential == "session" else "api_key"
        variant = {
            C.CONNECTIONS_EDIT: {"change": "saved"},
            C.CONNECTIONS_DISCONNECT: {"change": "disconnected"},
            C.PATH_MAPPINGS_CREATE: {"change": "created"},
            C.PATH_MAPPINGS_UPDATE: {"change": "updated"},
            C.PATH_MAPPINGS_DELETE: {"change": "deleted"},
            C.PATH_MAPPINGS_REORDER: {"change": "reordered"},
        }.get(case.capability, {})
        assert new_events == [
            (
                case.audited.value,
                "denied",
                refused_account,
                credential_kind,
                None,
                {"reason": "capability_denied", **variant},
            )
        ]
    else:
        assert new_events == []

    allowed = app.send(
        case.method, path, _headers(accounts, _account(holder), credential), body, case.content
    )

    assert allowed.status_code == case.status, allowed.text
    assert not any(secret in allowed.text for secret in SECRETS)
    succeeded = [event for event in app.audit()[len(audit_before) :] if event[1] == "succeeded"]
    if case.audited is not None:
        action, _outcome, actor, kind, _resource, _details = succeeded[-1]
        assert (action, actor, kind) == (case.audited.value, _account(holder), credential)
    else:
        assert succeeded == []
