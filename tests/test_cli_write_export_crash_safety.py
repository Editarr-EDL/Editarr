# ruff: noqa: F811
"""`editarr admin write-export` when the filesystem or the process misbehaves.

A file saved or linked during the command is never replaced or followed, a pending
recovery is left to MU-31, a failure or interrupt after placement removes only the
file the command created, a crashed run's temporary file is removed only when it is
the command's own, and without hard links the file is still created exclusively.
"""

import errno
import hashlib
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from scanner.persistence.repository import export_committed
from scanner.persistence.write_lock import write_lock
from scanner.security.account_ownership import transfer_ownership
from tests.cli_write_export_support import (
    _events,
    _refused,
    _write,
    jellyfin,
    published,
)
from tests.multi_user_access_support import ADMIN, OWNER, OWNER_PASSWORD
from tests.test_personal_exports_http import app, review_app

__all__ = ["app", "jellyfin", "published", "review_app"]


def _after_check(monkeypatch, change):
    """Run `change` once the destination passed its pathname checks."""
    original = export_committed.prepare_export_directory

    def prepared(*args, **kwargs):
        original(*args, **kwargs)
        change()

    monkeypatch.setattr(export_committed, "prepare_export_directory", prepared)


@pytest.mark.parametrize("seam", ["after_check", "before_placement"])
def test_a_file_saved_during_the_command_is_never_replaced(
    app, published, monkeypatch, capsys, seam
):
    export_id, path, content = published
    path.unlink()
    edited = content + b"\n* saved meanwhile\n"
    if seam == "after_check":
        _after_check(monkeypatch, lambda: path.write_bytes(edited))
    else:
        staged = export_committed.stage_event

        def stage_then_save(*args, **kwargs):
            path.write_bytes(edited)
            return staged(*args, **kwargs)

        monkeypatch.setattr(export_committed, "stage_event", stage_then_save)
    assert "export_file_collision" in _refused(monkeypatch, capsys, export_id)
    assert path.read_bytes() == edited
    assert not list(path.parent.glob(".*"))
    assert _events(app) == [("failed", OWNER, "export_file_collision")]


@pytest.mark.parametrize("level", ["id_directory", "format_directory"])
def test_a_directory_link_made_after_the_checks_never_leads_out(
    app, published, monkeypatch, capsys, tmp_path, level
):
    export_id, path, _content = published
    path.unlink()
    outside = tmp_path / "outside"
    directory = path.parent if level == "id_directory" else path.parent.parent

    def redirect():
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)

    _after_check(monkeypatch, redirect)
    assert "export_destination_changed" in _refused(monkeypatch, capsys, export_id)
    assert not [entry for entry in outside.rglob("*") if entry.is_file()]
    assert _events(app) == [("failed", OWNER, "export_destination_changed")]


def test_without_descriptor_anchoring_the_platform_is_refused(app, published, monkeypatch, capsys):
    export_id, path, _content = published
    path.unlink()
    monkeypatch.setattr(export_committed, "ANCHORED", False)
    assert "export_destination_unavailable" in _refused(monkeypatch, capsys, export_id)
    assert not path.exists()


def test_without_hard_links_the_file_is_created_exclusively(app, published, monkeypatch):
    export_id, path, content = published
    path.unlink()
    _unsupported_links(monkeypatch)
    _write(monkeypatch, export_id)
    assert path.read_bytes() == content
    assert not list(path.parent.glob(".*"))
    assert _events(app) == [("succeeded", OWNER, None)]


def test_a_former_owner_waiting_for_the_lock_is_refused(app, published, monkeypatch, capsys):
    export_id, path, _content = published
    path.unlink()
    app.accounts.link(ADMIN)
    real_lock = export_committed.publication_lock

    @contextmanager
    def ownership_moves_while_waiting(db):
        db.rollback()
        with app.accounts.factory() as other, write_lock(other):
            transfer_ownership(other, OWNER, ADMIN, now=datetime.now(UTC))
            other.commit()
        with real_lock(db):
            yield

    monkeypatch.setattr(export_committed, "publication_lock", ownership_moves_while_waiting)
    assert "capability_denied" in _refused(monkeypatch, capsys, export_id)
    assert not path.exists()
    assert _events(app) == [("failed", OWNER, "capability_denied")]


@pytest.mark.parametrize("driver", [False, True])
def test_a_raw_driver_error_is_a_concise_refusal(app, published, monkeypatch, capsys, driver):
    export_id, path, _content = published
    path.unlink()

    def full(connection, cursor, statement, *args):
        if statement.startswith("INSERT INTO audit_events"):
            if driver:
                raise sqlite3.OperationalError("database or disk is full")
            raise OperationalError(statement, {}, Exception("database or disk is full"))

    event.listen(app.accounts.engine, "before_cursor_execute", full)
    try:
        assert "the database refused the command" in _refused(monkeypatch, capsys, export_id)
    finally:
        event.remove(app.accounts.engine, "before_cursor_execute", full)
    assert not path.exists() and not list(path.parent.glob(".*"))


def _replace(path: Path, content: bytes) -> None:
    """Save `content` as a new file over `path`, as an editor that renames into place does."""
    staged = path.with_name("editor-save")
    staged.write_bytes(content)
    os.replace(staged, path)


@pytest.mark.parametrize("change", [None, "in_place", "replaced"])
def test_a_failed_commit_removes_only_the_file_it_created(
    app, published, monkeypatch, capsys, change
):
    export_id, path, content = published
    path.unlink()
    note = content + b"\n* edited before the commit failed\n"

    def fail(connection):
        if change == "in_place":
            with path.open("r+b") as stream:
                stream.write(note)
        elif change == "replaced":
            _replace(path, note)
        raise OperationalError("COMMIT", {}, Exception("disk I/O error"))

    event.listen(app.accounts.engine, "commit", fail)
    try:
        error = _refused(monkeypatch, capsys, export_id)
    finally:
        event.remove(app.accounts.engine, "commit", fail)
    assert "the database failed to record the write" in error
    assert _events(app) == []
    if change:
        assert f"changed and was left at {path}" in error and path.read_bytes() == note
    else:
        assert "the file it created was removed" in error and not path.exists()
    assert not list(path.parent.glob(".*"))


def test_a_file_swapped_in_before_verification_is_kept(app, published, monkeypatch, capsys):
    export_id, path, content = published
    path.unlink()
    note = content + b"\n* saved over the new file\n"
    synced = os.fsync

    def swap_after_placement(descriptor):
        synced(descriptor)
        if path.exists() and path.read_bytes() == content:
            _replace(path, note)

    monkeypatch.setattr(os, "fsync", swap_after_placement)
    assert "write_failed" in _refused(monkeypatch, capsys, export_id)
    assert path.read_bytes() == note
    assert _events(app) == [("failed", OWNER, "write_failed")]


def _journal(path: Path) -> Path:
    return path.with_name(f".{path.name}.editarr-recovery.json")


def _interrupted_write(path: Path, content: bytes) -> None:
    """Leave MU-31's recovery journal for a publish that stopped before placing `content`."""
    new = hashlib.sha256(content).hexdigest()
    record = {"version": 1, "old": None, "new": new, "backup": None, "temporary": None}
    _journal(path).write_text(json.dumps(record))


def test_a_pending_recovery_is_refused_and_left_to_recovery(app, published, monkeypatch, capsys):
    export_id, path, content = published
    path.unlink()
    _interrupted_write(path, content)
    journal = _journal(path).read_bytes()
    assert "export_recovery_required" in _refused(monkeypatch, capsys, export_id)
    assert not path.exists() and _journal(path).read_bytes() == journal
    assert _events(app) == [("failed", OWNER, "export_recovery_required")]


def test_a_recovery_journal_behind_a_late_directory_link_draws_nothing_out(
    app, published, monkeypatch, capsys, tmp_path
):
    export_id, path, content = published
    path.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    _interrupted_write(outside / path.name, content)
    resolved = export_committed.safe_destination

    def link_after_first_check(*args, **kwargs):
        destination = resolved(*args, **kwargs)
        if not path.parent.is_symlink():
            path.parent.rmdir()
            path.parent.symlink_to(outside, target_is_directory=True)
        return destination

    monkeypatch.setattr(export_committed, "safe_destination", link_after_first_check)
    assert "export_destination_changed" in _refused(monkeypatch, capsys, export_id)
    assert [entry.name for entry in outside.iterdir()] == [_journal(path).name]
    assert _events(app) == [("failed", OWNER, "export_destination_changed")]


def _after_placement(monkeypatch, path: Path, error: BaseException) -> None:
    """Raise `error` once the directory holding the newly placed file is synced."""
    synced = os.fsync

    def sync(descriptor):
        synced(descriptor)
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and path.exists():
            raise error

    monkeypatch.setattr(os, "fsync", sync)


def test_a_failure_after_placement_removes_the_file(app, published, monkeypatch, capsys):
    export_id, path, _content = published
    path.unlink()
    _after_placement(monkeypatch, path, OSError(errno.EIO, "Input/output error"))
    assert "write_failed" in _refused(monkeypatch, capsys, export_id)
    assert not path.exists() and not list(path.parent.glob(".*"))
    assert _events(app) == [("failed", OWNER, "write_failed")]


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt(), SystemExit(130)])
def test_an_interrupt_after_placement_removes_the_file(app, published, monkeypatch, interrupt):
    export_id, path, _content = published
    path.unlink()
    _after_placement(monkeypatch, path, interrupt)
    with pytest.raises(type(interrupt)):
        _write(monkeypatch, export_id)
    assert not path.exists() and not list(path.parent.glob(".*"))
    assert _events(app) == []


def test_a_rerun_removes_a_crashed_runs_temporary_file(app, published, monkeypatch, capsys):
    export_id, path, content = published
    path.unlink()
    unlink = os.unlink

    def crash_at_temporary(name, *args, **kwargs):
        if str(name).endswith(".tmp"):
            raise KeyboardInterrupt
        return unlink(name, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", crash_at_temporary)
    with pytest.raises(KeyboardInterrupt):
        _write(monkeypatch, export_id)
    monkeypatch.setattr(os, "unlink", unlink)
    (leftover,) = path.parent.glob(".*.tmp")
    unrelated = path.parent / ".editarr-write-export-user.tmp"
    unrelated.write_bytes(b"not the command's")
    _write(monkeypatch, export_id)
    assert path.read_bytes() == content and not leftover.exists()
    assert unrelated.read_bytes() == b"not the command's"
    assert _events(app) == [("succeeded", OWNER, None)]


def _temporary(path: Path, digit: str) -> Path:
    return path.with_name(f".editarr-write-export-{digit * 32}.tmp")


@pytest.mark.parametrize("state", ["missing", "present", "collision", "journal"])
def test_a_foreign_file_under_the_temporary_name_is_kept(app, published, monkeypatch, state):
    export_id, path, content = published
    if state != "present":
        path.unlink()
    if state == "collision":
        path.write_bytes(b"manual export")
    if state == "journal":
        _interrupted_write(path, content)
    foreign = _temporary(path, "a")
    foreign.write_bytes(b"a user's only copy of something else")
    try:
        _write(monkeypatch, export_id)
    except SystemExit as exit_info:
        assert exit_info.code == 1 and state in {"collision", "journal"}
    assert foreign.read_bytes() == b"a user's only copy of something else"


def test_a_refusal_leaves_a_crashed_runs_temporary_file(app, published, monkeypatch, capsys):
    export_id, path, content = published
    path.write_bytes(b"manual export")
    leftover = _temporary(path, "b")
    leftover.write_bytes(content)
    assert "export_file_collision" in _refused(monkeypatch, capsys, export_id)
    assert leftover.read_bytes() == content


@pytest.mark.parametrize("state", ["missing", "present"])
def test_a_success_removes_a_partial_temporary_file(app, published, monkeypatch, state):
    export_id, path, content = published
    if state == "missing":
        path.unlink()
    leftover = _temporary(path, "c")
    leftover.write_bytes(content[:5])
    _write(monkeypatch, export_id)
    assert path.read_bytes() == content and not leftover.exists()


def _interrupt(kind: str) -> None:
    if kind == "sigint":
        signal.raise_signal(signal.SIGINT)
    raise SystemExit(130)


@pytest.mark.parametrize("kind", ["sigint", "system_exit"])
def test_an_interrupt_as_the_link_returns_removes_the_file(app, published, monkeypatch, kind):
    """CPython delivers a Ctrl-C that lands during `linkat` as the call returns."""
    export_id, path, _content = published
    path.unlink()
    link = os.link

    def interrupted(*args, **kwargs):
        link(*args, **kwargs)
        _interrupt(kind)

    monkeypatch.setattr(os, "link", interrupted)
    with pytest.raises((KeyboardInterrupt, SystemExit)):
        _write(monkeypatch, export_id)
    assert not path.exists() and not list(path.parent.glob(".*"))
    assert _events(app) == []


def test_an_interrupt_as_placement_returns_removes_the_file(app, published, monkeypatch):
    export_id, path, _content = published
    path.unlink()
    create = export_committed._create

    def interrupted(*args):
        create(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(export_committed, "_create", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _write(monkeypatch, export_id)
    assert not path.exists() and not list(path.parent.glob(".*"))
    assert _events(app) == []


def test_a_file_saved_at_the_temporary_name_after_its_removal_is_kept(
    app, published, monkeypatch, capsys
):
    export_id, path, _content = published
    path.unlink()
    saved = []
    unlink = os.unlink

    def save_after_removal(name, *args, **kwargs):
        unlink(name, *args, **kwargs)
        if str(name).endswith(".tmp") and not saved:
            saved.append(path.parent / name)
            saved[0].write_bytes(b"saved under the freed name")

    monkeypatch.setattr(os, "unlink", save_after_removal)
    _after_placement(monkeypatch, path, OSError(errno.EIO, "Input/output error"))
    assert "write_failed" in _refused(monkeypatch, capsys, export_id)
    assert not path.exists() and saved[0].read_bytes() == b"saved under the freed name"


def _unsupported_links(monkeypatch) -> None:
    def unsupported(*args, **kwargs):
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(os, "link", unsupported)


def test_without_hard_links_an_interrupted_write_removes_the_partial_file(
    app, published, monkeypatch
):
    export_id, path, content = published
    path.unlink()
    _unsupported_links(monkeypatch)
    write = os.write

    def partial(descriptor, data):
        if path.exists() and os.path.samestat(os.fstat(descriptor), os.stat(path)):
            write(descriptor, bytes(data[:5]))
            raise KeyboardInterrupt
        return write(descriptor, data)

    monkeypatch.setattr(os, "write", partial)
    with pytest.raises(KeyboardInterrupt):
        _write(monkeypatch, export_id)
    assert not path.exists() and not list(path.parent.glob(".*"))
    assert _events(app) == []


FALLBACK_CRASH = """
import errno, os, sys
from scanner import cli
real_write, linked = os.write, []
def unsupported(*args, **kwargs):
    linked.append(True)
    raise OSError(errno.ENOTSUP, "no hard links")
def crash(descriptor, data):
    if linked:
        real_write(descriptor, bytes(data[:5]))
        os._exit(73)
    return real_write(descriptor, data)
os.link, os.write = unsupported, crash
sys.argv = ["editarr", "admin", "write-export", sys.argv[1], "--password-stdin"]
cli.main()
"""


def test_without_hard_links_a_hard_crash_leaves_a_partial_file_for_the_operator(
    app, published, monkeypatch, capsys, tmp_path
):
    export_id, path, content = published
    path.unlink()
    environment = {
        **os.environ,
        "EDITARR_DATA_DIR": str(app.accounts.path.parent),
        "EDITARR_LOG_DIR": str(tmp_path / "logs"),
    }
    crashed = subprocess.run(
        [sys.executable, "-c", FALLBACK_CRASH, str(export_id)],
        input=OWNER_PASSWORD,
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    assert crashed.returncode == 73, crashed.stderr
    assert path.read_bytes() == content[:5] and _events(app) == []
    assert "export_file_collision" in _refused(monkeypatch, capsys, export_id)
    assert path.read_bytes() == content[:5]
    path.unlink()
    _write(monkeypatch, export_id)
    assert path.read_bytes() == content and not list(path.parent.glob(".*"))
