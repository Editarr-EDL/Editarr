"""MU-17 HTTP harness: the real settings and operations routers behind the access middleware.

Accounts come from `live_accounts` (`mu04-access-builders-3`): owner 1, admins
2 and 3, users 4 and 5, each with a session and an API key. Only
external boundaries are replaced, and each replacement records its calls:
Jellyfin connection tests, the model acquisition service, the restart
controller, and the backup executor. `state()` fingerprints every setting these
routes can change, so a refused request can be shown to change nothing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text

import scanner.api as root
from scanner.backup.schedule_store import ScheduleConfig
from scanner.http import (
    api_system,
    backup_api,
    backup_schedule_api,
    catalog_settings_api,
    deployment_api,
    model_storage_api,
    restart_api,
    scan_catalog_api,
)
from scanner.model_acquisition import manifest
from scanner.models import (
    JellyfinSettingsUpdate,
    PathMappingCreate,
    RestartCapability,
)
from scanner.repository import create_path_mapping, update_jellyfin_settings
from scanner.settings.config import Settings
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import LiveAccounts
from tests.test_model_storage_api import _Service, _status

SECRETS = ("jellyfin-secret-key-0001", "replacement-key-0002")
ARTIFACT = "faster-whisper-base"
BACKEND = manifest().artifact(ARTIFACT).backend_ids[0]
ARCHIVE = b"editarr-archive-bytes-for-mu17"
_ROUTERS = (
    api_system.router,
    restart_api.router,
    deployment_api.router,
    catalog_settings_api.router,
    scan_catalog_api.router,
    model_storage_api.router,
    backup_api.router,
    backup_schedule_api.router,
)


class _Models(_Service):
    def __init__(self, calls: list[str]):
        super().__init__((_status(),))
        self.calls = calls

    def prefetch_backend(self, backend_id: str) -> None:
        self.calls.append(f"prefetch:{backend_id}")


class _Restart:
    def __init__(self, calls: list[str]):
        self.calls = calls
        self.capability = RestartCapability(
            supported=True,
            mode="standalone_controller",
            generation=1,
            in_progress=False,
            outcome="idle",
        )

    def reserve(self) -> None:
        self.calls.append("restart")
        self.capability.target_generation = 2

    def commit(self) -> None:
        self.calls.append("restart-commit")

    def cancel(self) -> None:
        self.calls.append("restart-cancel")


@dataclass
class OperationsApp:
    client: object
    accounts: LiveAccounts
    data: Path
    destination: Path
    calls: list[str] = field(default_factory=list)

    def send(self, method: str, path: str, headers: dict, body=None, content: bytes | None = None):
        self.client.cookies.clear()
        if content is not None:
            return self.client.request(method, path, headers=headers, content=content)
        return self.client.request(method, path, headers=headers, json=body)

    def seed_job(self, action: str, state: str) -> str:
        """Place one backup job in `state` without running the executor."""

        job_id = uuid.uuid4().hex
        directory = self.data / "backup-ui-jobs" / job_id
        directory.mkdir(parents=True)
        job = backup_api.BackupJob(
            job_id,
            action,
            directory,
            self.accounts.path,
            self.data,
            directory / "archive",
            state=state,
            plan={"kind": "full"} if state == "ready" and action == "restore" else None,
        )
        if state in {"ready", "uploaded"}:
            job.archive.write_bytes(ARCHIVE)
        backup_api._jobs[job_id] = job
        return job_id

    def schedule_body(self) -> dict:
        config = ScheduleConfig(
            enabled=True, interval_hours=2, keep_count=2, destination=str(self.destination)
        )
        return {**config.model_dump(), "confirmed": True}

    def state(self) -> str:
        """A digest of connections, mappings, setting files, backup jobs, and boundary calls."""

        with self.accounts.factory() as db:
            rows = [
                [tuple(row) for row in db.execute(text(f"SELECT * FROM {table} ORDER BY 1"))]
                for table in ("jellyfin_settings", "path_mappings")
            ]
        files = sorted(
            (str(path.relative_to(self.data)), path.read_bytes())
            for path in self.data.rglob("*")
            if path.is_file()
        )
        jobs = sorted(
            (job_id, job.state, job.cancelled) for job_id, job in backup_api._jobs.items()
        )
        snapshot = repr((rows, files, jobs, list(self.calls)))
        return hashlib.sha256(snapshot.encode()).hexdigest()

    def audit(self) -> list[tuple]:
        with self.accounts.factory() as db:
            return [
                (action, outcome, actor, credential, resource, json.loads(details))
                for action, outcome, actor, credential, resource, details in db.execute(
                    text(
                        "SELECT action, outcome, actor_id, credential, resource, details_json "
                        "FROM audit_events ORDER BY id"
                    )
                )
            ]


def serve_operations(monkeypatch, accounts: LiveAccounts, tmp_path: Path):
    """Start the harness; the caller closes `app.client` and clears `backup_api._jobs`."""

    data, logs, destination = tmp_path / "data", tmp_path / "logs", tmp_path / "backups"
    for directory in (data, logs, destination):
        directory.mkdir(mode=0o700)
    (logs / "application.log").write_text("started\n")
    configuration = Settings(data_dir=data, log_dir=logs, cache_dir=tmp_path / "cache")
    for module in (api_system, deployment_api):
        monkeypatch.setattr(module, "settings", configuration)
    location = lambda: (accounts.path, data)  # noqa: E731
    monkeypatch.setattr(backup_api, "database_location", location)
    monkeypatch.setattr(backup_schedule_api, "database_location", location)
    monkeypatch.setattr(backup_api._executor, "submit", lambda *_args: None)
    client = serve_access_fixture(monkeypatch, accounts)
    app = OperationsApp(client, accounts, data, destination)
    calls = app.calls
    monkeypatch.setattr(root, "_test_jellyfin_connection", lambda *_: calls.append("test:jellyfin"))
    models = _Models(calls)
    monkeypatch.setattr(root, "get_acquisition_service", lambda: models)
    monkeypatch.setattr(model_storage_api, "_backend_is_available", lambda _backend: True)
    for router in _ROUTERS:
        client.app.include_router(router)
    client.app.state.restart_client = _Restart(calls)
    with accounts.factory() as db:
        update_jellyfin_settings(
            db, JellyfinSettingsUpdate(server_url="http://jellyfin.test", api_key=SECRETS[0])
        )
        create_path_mapping(db, PathMappingCreate(remote_path="/remote/a", local_path="/local/a"))
    return app
