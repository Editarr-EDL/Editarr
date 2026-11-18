"""Cross-process maintenance tests use only temporary SQLite files."""

import multiprocessing
import os
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from scanner import db as database
from scanner.runtime.maintenance import DatabaseMaintenance, MaintenanceUnavailable


def _worker_holding_connection(data_dir, connection):
    database.settings = SimpleNamespace(
        data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'specimen.db'}"
    )
    engine = database.get_engine()
    try:
        with DatabaseMaintenance(data_dir).worker_lease():
            with engine.connect() as session:
                connection.send(session.execute(text("SELECT value FROM specimen")).scalar())
            while not DatabaseMaintenance(data_dir).requested():
                time.sleep(0.02)
        connection.send("closed")
    finally:
        engine.dispose()
        connection.close()


def _contending_owner(data_dir, connection):
    try:
        with DatabaseMaintenance(data_dir).exclusive(timeout=0.1):
            connection.send("acquired")
    except MaintenanceUnavailable as error:
        connection.send(str(error))
    finally:
        connection.close()


def _contending_checkout(data_dir, connection):
    database.settings = SimpleNamespace(
        data_dir=data_dir, database_url=f"sqlite:///{data_dir / 'specimen.db'}"
    )
    engine = database.get_engine()
    try:
        with engine.connect():
            connection.send("opened")
    except MaintenanceUnavailable as error:
        connection.send(str(error))
    finally:
        engine.dispose()
        connection.close()


def _contending_worker(data_dir, connection):
    try:
        with DatabaseMaintenance(data_dir).worker_lease():
            connection.send("started")
    except MaintenanceUnavailable as error:
        connection.send(str(error))
    finally:
        connection.close()


def _crashing_owner(data_dir, connection):
    with DatabaseMaintenance(data_dir).exclusive():
        connection.send("acquired")
        connection.close()
        os._exit(9)


def _child(target, data_dir):
    parent, child = multiprocessing.get_context("spawn").Pipe()
    process = multiprocessing.get_context("spawn").Process(target=target, args=(data_dir, child))
    process.start()
    child.close()
    return process, parent


def _finish(process, connection):
    connection.close()
    process.join(timeout=5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("maintenance test child did not exit")


@pytest.fixture
def specimen(tmp_path, monkeypatch):
    monkeypatch.setattr(
        database,
        "settings",
        SimpleNamespace(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'specimen.db'}"),
    )
    engine = database.get_engine()
    with engine.begin() as session:
        session.execute(text("CREATE TABLE specimen (value TEXT NOT NULL)"))
        session.execute(text("INSERT INTO specimen (value) VALUES ('original')"))
    try:
        yield tmp_path, engine
    finally:
        engine.dispose()


def test_maintenance_waits_for_surviving_worker_and_blocks_new_checkouts(specimen):
    data_dir, engine = specimen
    process, connection = _child(_worker_holding_connection, data_dir)
    try:
        assert connection.poll(5)
        assert connection.recv() == "original"
        acquired = threading.Event()
        release = threading.Event()
        outcome = []

        def maintain():
            try:
                with DatabaseMaintenance(data_dir).exclusive(timeout=3):
                    acquired.set()
                    release.wait(3)
            except Exception as error:
                outcome.append(error)

        owner = threading.Thread(target=maintain)
        owner.start()
        try:
            assert connection.poll(5)
            assert connection.recv() == "closed"
            assert acquired.wait(5)
            with pytest.raises(MaintenanceUnavailable):
                with engine.connect():
                    pass
            contender, contender_connection = _child(_contending_owner, data_dir)
            try:
                assert contender_connection.poll(5)
                assert "Another database maintenance owner" in contender_connection.recv()
            finally:
                _finish(contender, contender_connection)
        finally:
            release.set()
            owner.join(timeout=5)
        assert not owner.is_alive()
        assert outcome == []
        with engine.connect() as session:
            assert session.execute(text("SELECT value FROM specimen")).scalar() == "original"
    finally:
        _finish(process, connection)
    assert process.exitcode == 0


def test_pooled_connection_reopens_after_database_generation_switch(specimen):
    data_dir, engine = specimen
    with engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM specimen")).scalar() == "original"
    replacement = data_dir / "replacement.db"
    with sqlite3.connect(replacement) as connection:
        connection.execute("CREATE TABLE specimen (value TEXT NOT NULL)")
        connection.execute("INSERT INTO specimen VALUES ('restored')")
    with DatabaseMaintenance(data_dir).exclusive():
        os.replace(replacement, data_dir / "specimen.db")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM specimen")).scalar() == "restored"


def test_unresolved_restore_journal_blocks_existing_engine_checkout(specimen):
    data_dir, engine = specimen
    with engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM specimen")).scalar() == "original"
    journal = data_dir / "restore-activation.json"
    journal.write_text("pending")
    with pytest.raises(MaintenanceUnavailable, match="Restore recovery"):
        with engine.connect():
            pass
    journal.unlink()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT value FROM specimen")).scalar() == "original"


def test_timeout_preserves_original_and_clears_pending(specimen):
    data_dir, engine = specimen
    with engine.connect() as session:
        with pytest.raises(MaintenanceUnavailable, match="Timed out"):
            with DatabaseMaintenance(data_dir).exclusive(timeout=0.1):
                pytest.fail("exclusive access was granted with an active database user")
        assert session.execute(text("SELECT value FROM specimen")).scalar() == "original"
    assert not DatabaseMaintenance(data_dir).pending_path.exists()
    with DatabaseMaintenance(data_dir).exclusive(timeout=1):
        with engine.connect() as session:
            assert session.execute(text("SELECT value FROM specimen")).scalar() == "original"
        contender, contender_connection = _child(_contending_checkout, data_dir)
        try:
            assert contender_connection.poll(5)
            assert "maintenance is pending" in contender_connection.recv().lower()
        finally:
            _finish(contender, contender_connection)
        worker, worker_connection = _child(_contending_worker, data_dir)
        try:
            assert worker_connection.poll(5)
            assert "maintenance is pending" in worker_connection.recv().lower()
        finally:
            _finish(worker, worker_connection)


def test_owner_crash_recovers_stale_state_without_changing_data(specimen):
    data_dir, engine = specimen
    process, connection = _child(_crashing_owner, data_dir)
    try:
        assert connection.poll(5)
        assert connection.recv() == "acquired"
    finally:
        _finish(process, connection)
    assert process.exitcode == 9
    assert DatabaseMaintenance(data_dir).pending_path.exists()
    with engine.connect() as session:
        assert session.execute(text("SELECT value FROM specimen")).scalar() == "original"
    assert not DatabaseMaintenance(data_dir).pending_path.exists()
    with DatabaseMaintenance(data_dir).exclusive():
        pass


def test_windows_connection_leases_are_shared_and_maintenance_is_exclusive(monkeypatch):
    from scanner.runtime import maintenance

    holders: dict[int, int] = {}

    class Kernel32:
        """Win32 byte-range semantics: shared locks coexist, exclusive locks do not."""

        def LockFileEx(self, handle, flags, _reserved, _low, _high, _region):  # noqa: N802
            exclusive = bool(flags & maintenance._LOCKFILE_EXCLUSIVE_LOCK)
            assert flags & maintenance._LOCKFILE_FAIL_IMMEDIATELY
            if (exclusive and holders) or -1 in holders.values():
                return 0
            holders[handle] = -1 if exclusive else 1
            return 1

        def UnlockFileEx(self, handle, *_args):  # noqa: N802
            return int(holders.pop(handle, None) is not None)

    monkeypatch.setattr(maintenance, "_kernel32", Kernel32)
    monkeypatch.setattr(maintenance, "_windows_handle", lambda fd: fd)
    monkeypatch.setattr(maintenance.os, "name", "nt")

    # Two pooled connections (e.g. middleware and route sessions) hold leases at once.
    assert maintenance._lock(11, exclusive=False, blocking=False)
    assert maintenance._lock(12, exclusive=False, blocking=False)
    assert not maintenance._lock(13, exclusive=True, blocking=False)
    maintenance._unlock(11)
    maintenance._unlock(12)
    assert maintenance._lock(13, exclusive=True, blocking=False)
    assert not maintenance._lock(11, exclusive=False, blocking=False)
