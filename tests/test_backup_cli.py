"""Disposable subprocess coverage for the offline portable-backup interface."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from scanner.backup.activation import HOLD, JOURNAL
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security.community_identity import load_or_create_installation_identity
from tests.community_identity_support import identity_home


def _run(data_dir, *arguments, password=None, confirmation=None):
    payload = f"{password}\n" if password is not None else ""
    if confirmation is not None:
        payload += f"{confirmation}\n"
    return subprocess.run(
        [sys.executable, "-m", "scanner.cli", "--data-dir", str(data_dir), "backup", *arguments],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "EDITARR_DATA_DIR": str(data_dir)},
    )


def _installation(directory, name):
    directory.mkdir(mode=0o700)
    migrate_fresh_database(directory / "scanner.db", directory)
    with sqlite3.connect(directory / "scanner.db") as connection:
        connection.execute(
            "INSERT INTO jellyfin_settings (id, api_key, updated_at) VALUES (1, ?, ?)",
            (name, "2026-09-27"),
        )
    load_or_create_installation_identity(identity_home(directory))


@pytest.mark.parametrize("encrypted", [False, True])
def test_backup_cli_restores_into_an_empty_data_directory(tmp_path, encrypted):
    if encrypted and not (shutil.which("age") and shutil.which("expect")):
        pytest.skip("Optional source age transport not installed")
    source, target = tmp_path / "source", tmp_path / "target"
    _installation(source, "source-secret")
    target.mkdir(mode=0o700)
    archive = tmp_path / "portable.backup"
    password = "disposable-passphrase" if encrypted else None
    export_options = ("--encrypt", "--password-stdin") if encrypted else ()
    result = _run(source, "export", str(archive), *export_options, password=password)
    assert result.returncode == 0, result.stderr
    assert archive.is_file()
    assert "Exporting backup" in result.stderr
    inspect_options = ("--password-stdin",) if encrypted else ()
    result = _run(target, "inspect", str(archive), *inspect_options, password=password)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert (plan["kind"], plan["protection"]) == ("full", "age-scrypt" if encrypted else "plain")
    assert "source-secret" not in result.stdout
    if encrypted:
        wrong = _run(target, "inspect", str(archive), *inspect_options, password="wrong-value")
        assert wrong.returncode == 1
        assert not (target / HOLD).exists()
    assert _run(target, "restore", str(archive), "--replace-local-work").returncode == 2
    original = sorted(path.name for path in target.iterdir())
    cancelled = _run(target, "restore", str(archive), *inspect_options, password=password)
    assert cancelled.returncode == 2
    assert sorted(path.name for path in target.iterdir()) == original
    assert not (target / HOLD).exists()
    result = _run(
        target,
        "restore",
        str(archive),
        *inspect_options,
        "--confirm",
        password=password,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.split("\nRestore activated;")[0]) == plan
    assert "Review media paths and source connections" in result.stdout
    assert "0 transfer or link move records disagree" in result.stdout
    with sqlite3.connect(target / "scanner.db") as connection:
        assert connection.execute("SELECT api_key FROM jellyfin_settings").fetchone() == (
            "source-secret",
        )
    assert json.loads((target / HOLD).read_text())["queue_paused"] is True
    assert not (target / JOURNAL).exists()
    for output in (result.stdout, result.stderr):
        assert password is None or password not in output
        assert "source-secret" not in output
    refused = _run(
        source, "restore", str(archive), *inspect_options, "--confirm", password=password
    )
    assert refused.returncode == 1
    assert "restore_target_has_work" in refused.stderr


def test_backup_cli_refuses_an_unknown_schema_without_changing_it(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _installation(source, "source-secret")
    archive = tmp_path / "plain.backup"
    assert _run(source, "export", str(archive)).returncode == 0
    target.mkdir(mode=0o700)
    migrate_fresh_database(target / "scanner.db", target)
    with sqlite3.connect(target / "scanner.db") as connection:
        connection.execute("UPDATE alembic_version SET version_num = '9999_unknown_schema'")
    before = {path.name: path.read_bytes() for path in target.iterdir() if path.is_file()}
    for arguments in (("inspect", str(archive)), ("restore", str(archive), "--confirm")):
        refused = _run(target, *arguments)
        assert refused.returncode == 1
        assert "restore_target_unsupported_schema" in refused.stderr
        assert "9999_unknown_schema" in refused.stderr
    assert {path.name: path.read_bytes() for path in target.iterdir() if path.is_file()} == before


def test_backup_cli_plain_never_reads_password_and_rejects_argument_secret(tmp_path):
    source = tmp_path / "source"
    _installation(source, "source-secret")
    archive = tmp_path / "plain.backup"
    assert _run(source, "export", str(archive)).returncode == 0
    for arguments in (
        ("export", str(tmp_path / "other"), "--password-stdin"),
        ("inspect", str(archive), "--password-stdin"),
        ("export", str(tmp_path / "other"), "--password", "private-value"),
    ):
        result = _run(source, *arguments, password="private-value")
        assert result.returncode != 0
        assert "private-value" not in result.stdout + result.stderr
    encrypted_destination = tmp_path / "encrypted.backup"
    missing_input = _run(source, "export", str(encrypted_destination), "--encrypt")
    assert missing_input.returncode == 1
    assert "use --password-stdin" in missing_input.stderr
    assert "Traceback" not in missing_input.stderr
    assert not encrypted_destination.exists()
    assert _run(source, "recover").returncode == 0


def test_backup_cli_recovers_interrupted_switch_before_next_export(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    _installation(source, "source-secret")
    target.mkdir(mode=0o700)
    migrate_fresh_database(target / "scanner.db", target)
    archive = tmp_path / "portable.backup"
    assert _run(source, "export", str(archive)).returncode == 0
    script = (
        "import os, sys; from pathlib import Path; "
        "from scanner.backup import activation as a; "
        "from scanner.backup.restore import preview_restore; "
        "original = a._record; "
        "a._record = lambda path, data: (original(path, data), os._exit(47)) "
        "if path.name == a.JOURNAL else original(path, data); "
        "context = preview_restore(Path(sys.argv[1]), Path(sys.argv[2])); "
        "preview = context.__enter__(); "
        "a.activate_restore(preview, Path(sys.argv[2]) / 'scanner.db', Path(sys.argv[2]), "
        "confirmed=True)"
    )
    result = subprocess.run([sys.executable, "-c", script, str(archive), str(target)], check=False)
    assert result.returncode == 47
    assert (target / JOURNAL).exists()
    blocked = _run(target, "export", str(tmp_path / "blocked.backup"))
    assert blocked.returncode == 1
    assert "offline recovery" in blocked.stderr
    recovered = _run(target, "recover")
    assert recovered.returncode == 0, recovered.stderr
    assert "rolled back" in recovered.stdout
    assert not (target / JOURNAL).exists()
    with sqlite3.connect(target / "scanner.db") as connection:
        assert connection.execute("SELECT count(*) FROM jellyfin_settings").fetchone() == (0,)
