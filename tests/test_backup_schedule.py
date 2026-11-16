"""Scheduled backup tests use synthetic secrets and disposable destinations only."""

import json
import os

import pytest
from pydantic import ValidationError

from scanner.backup.archive import BackupError
from scanner.backup.schedule_store import (
    ScheduleConfig,
    ScheduleState,
    destination_identity,
    read_passphrase,
    read_state,
    write_state,
)
from tests.community_identity_support import identity_home


@pytest.fixture
def schedule_paths(tmp_path):
    data = tmp_path / "data"
    destination = tmp_path / "backups"
    data.mkdir(mode=0o700)
    destination.mkdir(mode=0o700)
    return data, destination


def configuration(output_directory, **values):
    return ScheduleConfig.model_validate(
        {
            "enabled": True,
            "interval_hours": 2,
            "keep_count": 2,
            "destination": str(output_directory),
            **values,
        }
    )


def test_schedule_defaults_disabled_and_round_trips_private_state(schedule_paths):
    data, destination = schedule_paths
    assert read_state(data).config is None
    state = ScheduleState(config=configuration(destination), next_run=3600.0)
    write_state(data, state)
    assert read_state(data) == state
    if os.name == "posix":
        assert (data / "backup-schedule.json").stat().st_mode & 0o777 == 0o600
    assert not list(data.glob(".backup-schedule-*"))


@pytest.mark.parametrize(
    "values",
    [
        {"interval_hours": 0},
        {"interval_hours": True},
        {"keep_count": 0},
        {"keep_count": 1001},
        {"destination": "relative"},
        {"protection": "age-scrypt"},
        {"passphrase_file": "/not-for-plain"},
        {"kind": "settings"},
    ],
)
def test_schedule_rejects_implicit_or_invalid_policy(schedule_paths, values):
    with pytest.raises(ValidationError):
        configuration(schedule_paths[1], **values)


def test_a_saved_settings_schedule_loads_as_full_with_a_notice(schedule_paths):
    from scanner.backup import schedule

    data, destination = schedule_paths
    saved = {
        "version": 1,
        "config": {
            "enabled": True,
            "interval_hours": 2,
            "keep_count": 2,
            "destination": str(destination),
            "kind": "settings",
        },
        "owner": "a" * 32,
    }
    (data / "backup-schedule.json").write_text(json.dumps(saved))
    state = read_state(data)
    assert state.config.kind == "full"
    status = schedule.schedule_status(data)
    assert (status["config"]["kind"], status["notice"]) == ("full", "settings_schedule_now_full")
    schedule.save_schedule(data, configuration(destination), now=0.0)
    assert schedule.schedule_status(data)["notice"] is None


def test_destination_must_exist_and_be_outside_data(schedule_paths):
    data, destination = schedule_paths
    assert destination_identity(configuration(destination), data) == (
        destination.stat().st_dev,
        destination.stat().st_ino,
    )
    with pytest.raises(BackupError):
        destination_identity(configuration(destination / "missing"), data)
    assert not (destination / "missing").exists()
    with pytest.raises(BackupError):
        destination_identity(configuration(data), data)


@pytest.mark.skipif(os.name != "posix", reason="POSIX protected credential contract")
def test_encrypted_schedule_reads_external_private_file_each_time(schedule_paths, tmp_path):
    data, destination = schedule_paths
    secret = tmp_path / "passphrase"
    secret.write_text("synthetic-first\n")
    secret.chmod(0o600)
    config = configuration(destination, protection="age-scrypt", passphrase_file=str(secret))
    assert read_passphrase(config, data) == "synthetic-first"
    secret.write_text("synthetic-second")
    assert read_passphrase(config, data) == "synthetic-second"
    write_state(data, ScheduleState(config=config))
    assert "synthetic" not in json.dumps(read_state(data).model_dump())
    secret.chmod(0o644)
    with pytest.raises(BackupError):
        read_passphrase(config, data)
    secret.unlink()
    with pytest.raises(OSError):
        read_passphrase(config, data)


@pytest.mark.skipif(os.name != "posix", reason="POSIX protected credential contract")
def test_credential_cannot_be_a_symlink_or_in_backup_contents(schedule_paths, tmp_path):
    data, destination = schedule_paths
    secret = tmp_path / "passphrase"
    secret.write_text("synthetic")
    secret.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(OSError):
        read_passphrase(
            configuration(destination, protection="age-scrypt", passphrase_file=str(link)), data
        )
    for root in (data, destination):
        with pytest.raises(BackupError):
            read_passphrase(
                configuration(
                    destination, protection="age-scrypt", passphrase_file=str(root / "key")
                ),
                data,
            )


def test_corrupt_schedule_fails_closed(schedule_paths):
    data, _destination = schedule_paths
    (data / "backup-schedule.json").write_text('{"config":{"enabled":true}}')
    with pytest.raises(BackupError):
        read_state(data)


@pytest.fixture
def scheduled_export(schedule_paths, monkeypatch):
    from scanner.backup import schedule

    data, destination = schedule_paths
    calls = []

    def export(_database, _data, output, **options):
        assert options["replace_existing"] is False
        assert options["require_reconciled"] is True
        with output.open("xb") as stream:
            stream.write(f"verified-{len(calls)}".encode())
        calls.append(options)

    monkeypatch.setattr(schedule, "export_backup", export)
    schedule.save_schedule(data, configuration(destination), now=0.0)
    return schedule, data, destination, calls


def test_due_boundary_catchup_restart_and_disable(scheduled_export):
    schedule, data, _destination, calls = scheduled_export
    database = data / "scanner.db"
    assert not schedule.run_due(database, data, clock=lambda: 7199)
    assert schedule.run_due(database, data, clock=lambda: 7200)
    assert len(calls) == 1
    assert not schedule.run_due(database, data, clock=lambda: 7200)
    assert schedule.run_due(database, data, clock=lambda: 72000)
    assert read_state(data).next_run == 79200
    assert len(calls) == 2
    schedule.disable_schedule(data)
    assert not schedule.run_due(database, data, clock=lambda: 100000)
    assert len(calls) == 2


def test_count_retention_only_deletes_oldest_owned_successes(scheduled_export):
    schedule, data, destination, _calls = scheduled_export
    manual = destination / "manual.backup"
    manual.write_bytes(b"keep-manual")
    for now in (7200, 14400, 21600):
        assert schedule.run_due(data / "scanner.db", data, clock=lambda: now)
    state = read_state(data)
    directory = destination / f"editarr-scheduled-{state.owner}"
    assert len(state.archives) == 2
    assert [record.created_at for record in state.archives] == [14400, 21600]
    assert len(list(directory.glob("*.backup"))) == 2
    assert manual.read_bytes() == b"keep-manual"


def test_export_failure_preserves_backups_and_retries_next_interval(scheduled_export, monkeypatch):
    schedule, data, destination, calls = scheduled_export
    assert schedule.run_due(data / "scanner.db", data, clock=lambda: 7200)
    prior = read_state(data)

    def fail(*_args, **_kwargs):
        raise OSError("disk full /private/secret-canary")

    monkeypatch.setattr(schedule, "export_backup", fail)
    assert schedule.run_due(data / "scanner.db", data, clock=lambda: 14400)
    state = read_state(data)
    assert state.outcome == "failed" and state.error == "export_failed"
    assert state.last_success == 7200 and state.next_run == 21600
    assert state.archives == prior.archives
    assert "secret-canary" not in json.dumps(schedule.schedule_status(data))
    assert not schedule.run_due(data / "scanner.db", data, clock=lambda: 14401)
    assert len(calls) == 1
    assert (destination / f"editarr-scheduled-{state.owner}" / state.archives[0].name).is_file()


def test_overlapping_run_config_changes_and_restore_are_blocked(scheduled_export):
    schedule, data, destination, calls = scheduled_export
    with schedule.schedule_lock(data):
        assert not schedule.run_due(data / "scanner.db", data, clock=lambda: 7200)
        with pytest.raises(schedule.ScheduleBusy):
            schedule.save_schedule(data, configuration(destination), now=0.0)
        with pytest.raises(schedule.ScheduleBusy):
            schedule.disable_schedule(data)
    (data / "restore-reconciliation.json").write_text("{}")
    assert not schedule.run_due(data / "scanner.db", data, clock=lambda: 7200)
    assert schedule.schedule_status(data)["restore_hold"] is True
    assert not calls


def test_interrupted_attempt_does_not_repeat_before_saved_next_run(scheduled_export):
    schedule, data, _destination, calls = scheduled_export
    state = read_state(data)
    state.outcome = "running"
    state.last_attempt = 7200.0
    state.next_run = 14400.0
    write_state(data, state)
    assert not schedule.run_due(data / "scanner.db", data, clock=lambda: 7201)
    assert read_state(data).outcome == "interrupted"
    assert not calls
    assert schedule.run_due(data / "scanner.db", data, clock=lambda: 14400)
    assert len(calls) == 1


def test_modified_owned_file_is_not_pruned(scheduled_export):
    schedule, data, destination, _calls = scheduled_export
    schedule.run_due(data / "scanner.db", data, clock=lambda: 7200)
    state = read_state(data)
    original = destination / f"editarr-scheduled-{state.owner}" / state.archives[0].name
    original.write_bytes(b"changed")
    schedule.run_due(data / "scanner.db", data, clock=lambda: 14400)
    schedule.run_due(data / "scanner.db", data, clock=lambda: 21600)
    assert original.read_bytes() == b"changed"
    state = read_state(data)
    assert state.error == "retention_requires_review"
    assert state.outcome == "succeeded" and state.last_success == 21600
    assert len(state.archives) == 3


def test_missing_credential_never_downgrades_or_deletes(scheduled_export, tmp_path):
    schedule, data, destination, calls = scheduled_export
    secret = tmp_path / "scheduled-key"
    secret.write_text("synthetic-secret")
    secret.chmod(0o600)
    schedule.save_schedule(
        data,
        configuration(destination, protection="age-scrypt", passphrase_file=str(secret)),
        now=0.0,
    )
    secret.unlink()
    assert schedule.run_due(data / "scanner.db", data, clock=lambda: 7200)
    assert read_state(data).error == "credential_unavailable"
    assert not calls


@pytest.mark.parametrize("encrypted", [False, True])
def test_real_scheduled_archives_restore_and_exclude_schedule_and_credential(
    schedule_paths,
    tmp_path,
    encrypted,
):
    import shutil
    import tarfile

    from scanner.backup import schedule
    from scanner.backup.age_transport import run_age
    from scanner.backup.archive import export_backup, validate_plain_archive
    from scanner.backup.restore import preview_restore
    from scanner.persistence.migrations.runner import migrate_fresh_database
    from scanner.security.community_identity import load_or_create_installation_identity

    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional source age transport not installed")
    data, destination = schedule_paths
    database = data / "scanner.db"
    migrate_fresh_database(database, data)
    identity = load_or_create_installation_identity(identity_home(data))
    secret = tmp_path / "private-passphrase"
    secret.write_text("synthetic-scheduled-passphrase\n")
    secret.chmod(0o600)
    options = {}
    if encrypted:
        options.update(protection="age-scrypt", passphrase_file=str(secret))
    schedule.save_schedule(data, configuration(destination, **options), now=0.0)
    assert schedule.run_due(database, data, clock=lambda: 7200)
    state = read_state(data)
    assert state.outcome == "succeeded", state.error
    archive = destination / f"editarr-scheduled-{state.owner}" / state.archives[0].name
    original = archive.read_bytes()
    with pytest.raises(FileExistsError):
        export_backup(database, data, archive, replace_existing=False)
    assert archive.read_bytes() == original
    password = "synthetic-scheduled-passphrase" if encrypted else None
    with preview_restore(archive, tmp_path / "restored", passphrase=password) as preview:
        assert preview.plan["kind"] == "full"
        assert preview.plan["source_identity"]["key_id"] == identity.key_id
    decoded = archive
    if encrypted:
        decoded = tmp_path / "decoded.backup"
        run_age(archive, decoded, password, decrypt=True)
    manifest = validate_plain_archive(decoded, protection="age-scrypt" if encrypted else "plain")
    assert manifest["identity_included"] is True
    with tarfile.open(decoded) as content:
        assert "backup-schedule.json" not in content.getnames()
        assert "private-passphrase" not in content.getnames()


def test_schedule_api_requires_explicit_confirmation_and_hides_secret_paths_on_error(
    schedule_paths,
    monkeypatch,
    tmp_path,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from scanner.http import backup_schedule_api
    from tests.operations_access_support import as_owner

    data, destination = schedule_paths
    monkeypatch.setattr(backup_schedule_api, "database_location", lambda: (data / "db", data))
    app = FastAPI()
    app.include_router(backup_schedule_api.router)
    as_owner(app, tmp_path / "access").close()
    with TestClient(app) as client:
        assert client.get("/api/backups/schedule").json()["config"] is None
        body = configuration(destination).model_dump()
        assert client.put("/api/backups/schedule", json=body).status_code == 422
        response = client.put("/api/backups/schedule", json={**body, "confirmed": True})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["config"]["enabled"] is True
        assert client.delete("/api/backups/schedule").json()["config"]["enabled"] is False
        body["destination"] = "/nonexistent/private-path-canary"
        response = client.put("/api/backups/schedule", json={**body, "confirmed": True})
        assert response.status_code == 400
        assert "private-path-canary" not in response.text


def test_runner_checks_on_start_and_stops_without_waiting_for_another_interval(
    schedule_paths,
    monkeypatch,
):
    import threading

    from scanner.backup import schedule_runner

    data, _destination = schedule_paths
    called = threading.Event()
    calls = []

    def tick(database, directory):
        calls.append((database, directory))
        called.set()

    monkeypatch.setattr(schedule_runner, "run_due", tick)
    scheduler = schedule_runner.BackupScheduler(data / "db", data)
    scheduler.start()
    assert called.wait(2)
    scheduler.stop()
    assert not scheduler.thread.is_alive()
    assert calls == [(data / "db", data)]


def test_process_exit_releases_lock_but_retains_durable_attempt(scheduled_export):
    import subprocess
    import sys

    schedule, data, _destination, calls = scheduled_export
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys; from pathlib import Path; from scanner.backup import schedule; "
            "schedule.export_backup=lambda *args,**kwargs: os._exit(42); "
            "directory=Path(sys.argv[1]); "
            "schedule.run_due(directory/'scanner.db',directory,clock=lambda:7200)",
            str(data),
        ],
        check=False,
        capture_output=True,
    )
    assert process.returncode == 42, process.stderr.decode()
    assert read_state(data).outcome == "running"
    assert not schedule.run_due(data / "scanner.db", data, clock=lambda: 7201)
    assert read_state(data).outcome == "interrupted"
    assert not calls
    assert schedule.run_due(data / "scanner.db", data, clock=lambda: 14400)
    assert len(calls) == 1


def test_export_rechecks_restore_hold_after_exclusive_lock(schedule_paths, monkeypatch):
    from contextlib import contextmanager

    from scanner.backup import archive
    from scanner.runtime.maintenance import DatabaseMaintenance

    data, destination = schedule_paths
    original = DatabaseMaintenance.exclusive

    @contextmanager
    def raced_lock(owner, **options):
        with original(owner, **options):
            (data / "restore-reconciliation.json").write_text("{}")
            yield

    monkeypatch.setattr(DatabaseMaintenance, "exclusive", raced_lock)
    with pytest.raises(BackupError, match="reconciliation"):
        archive.export_backup(
            data / "scanner.db",
            data,
            destination / "scheduled.backup",
            require_reconciled=True,
            replace_existing=False,
        )
    assert not (destination / "scheduled.backup").exists()
