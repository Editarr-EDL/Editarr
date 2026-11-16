"""MU-13: sealing legacy plaintext never logs a key and cleans the files until none remains.

A failed sealing write must not render the key through SQLAlchemy's parameter
text, including through real Uvicorn startup. Cleanup of replaced plaintext
survives a busy checkpoint, a failed VACUUM, and a hard exit after the rows are
sealed, and finishes on the next seal.
"""

import os
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from scanner.persistence.migrations import adoption
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import seal_plaintext_api_keys
from tests.test_api_key_migration import KEY, MEDIA_ACCESS, _keys, _seal, _with_key

READABLE = "0009_readable_api_key"
_STARTUP = """
import sqlite3
from sqlalchemy import Engine, event

def failed_write(cursor, statement, parameters, context):
    if statement.startswith("UPDATE api_keys"):
        raise sqlite3.OperationalError("database or disk is full")

event.listen(Engine, "do_execute", failed_write)
import scanner.api
import uvicorn
uvicorn.run(scanner.api.app, host="127.0.0.1", port=0)
"""
_INTERRUPTED_CLEANUP = """
import os, sqlite3, sys
from pathlib import Path
from sqlalchemy import Engine, event
from scanner.security.api_keys import seal_database_api_keys

def interrupt(cursor, statement, parameters, context):
    if statement == "VACUUM":
        if sys.argv[3] == "exit":
            os._exit(96)
        raise sqlite3.OperationalError("database or disk is full")

event.listen(Engine, "do_execute", interrupt)
print(seal_database_api_keys(Path(sys.argv[1]), Path(sys.argv[2])))
"""


def _legacy(tmp_path, revision=MEDIA_ACCESS):
    path, readable = _with_key(tmp_path, revision)
    assert readable
    adoption.upgrade_existing_database(path, path.parent)
    return path


def _remnant(path):
    files = (path, path.with_name(path.name + "-wal"))
    return any(KEY.encode() in file.read_bytes() for file in files if file.exists())


def _vacuums(engine):
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if statement == "VACUUM":
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    return seen


def test_a_failed_sealing_write_never_renders_the_key(tmp_path):
    path = _legacy(tmp_path)
    engine = create_engine(f"sqlite:///{path}")

    def failed_write(cursor, statement, parameters, context):
        if statement.startswith("UPDATE api_keys"):
            raise sqlite3.OperationalError("database or disk is full")

    event.listen(engine, "do_execute", failed_write)
    try:
        with Session(engine) as db, pytest.raises(OperationalError) as caught:
            seal_plaintext_api_keys(db, load_api_key_wrap_key(path.parent))
    finally:
        engine.dispose()
    assert "database or disk is full" in str(caught.value)
    assert KEY not in str(caught.value)
    assert _keys(path)[0][1] == KEY


def test_a_startup_that_cannot_seal_logs_no_key(tmp_path):
    path = _legacy(tmp_path)
    logs = tmp_path / "logs"
    environment = os.environ | {"EDITARR_DATA_DIR": str(path.parent), "EDITARR_LOG_DIR": str(logs)}
    result = subprocess.run(
        [sys.executable, "-B", "-c", _STARTUP],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    text = result.stdout + result.stderr + "".join(f.read_text() for f in logs.rglob("*.log"))
    assert result.returncode != 0
    assert "database or disk is full" in text
    assert KEY not in text
    assert _keys(path)[0][1] == KEY


def test_cleanup_blocked_by_a_reader_finishes_on_a_later_seal(tmp_path):
    path = _legacy(tmp_path)
    wrap = load_api_key_wrap_key(path.parent)
    reader = sqlite3.connect(path, isolation_level=None)
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute("BEGIN")
    reader.execute("SELECT token_value FROM api_keys").fetchall()
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 0.05})
    try:
        with Session(engine) as db:
            assert seal_plaintext_api_keys(db, wrap) == 1
        assert _keys(path)[0][1] is None
        assert _remnant(path)
        with Session(engine) as db:
            assert seal_plaintext_api_keys(db, wrap) == 0
        assert _remnant(path)
        reader.execute("ROLLBACK")
        reader.close()
        with Session(engine) as db:
            assert seal_plaintext_api_keys(db, wrap) == 0
        assert not _remnant(path)
        vacuums = _vacuums(engine)
        with Session(engine) as db:
            assert seal_plaintext_api_keys(db, wrap) == 0
        assert vacuums == []
    finally:
        reader.close()
        engine.dispose()


@pytest.mark.parametrize(("interruption", "exit_code"), [("exit", 96), ("full", 0)])
def test_cleanup_interrupted_after_the_rows_are_sealed_finishes_on_restart(
    tmp_path, interruption, exit_code
):
    path = _legacy(tmp_path, READABLE)
    # The upgrade's VACUUM compacted the file; free pages that still hold the key are
    # what an interrupted cleanup leaves behind.
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE remnant (value BLOB)")
        database.execute("INSERT INTO remnant VALUES (?)", (KEY.encode() * 64,))
    with sqlite3.connect(path) as database:
        database.execute("DROP TABLE remnant")
    assert _remnant(path)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            _INTERRUPTED_CLEANUP,
            str(path),
            str(path.parent),
            interruption,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == exit_code, result.stderr
    assert _keys(path)[0][1] is None
    assert _remnant(path)

    assert _seal(path) == 0

    assert not _remnant(path)
    assert KEY not in result.stdout + result.stderr


def test_the_wrap_key_repr_hides_its_bytes(tmp_path):
    wrap = load_api_key_wrap_key(tmp_path)
    assert repr(wrap._value) not in repr(wrap)
    assert wrap._value.hex() not in repr(wrap)
    assert wrap.key_id in repr(wrap)
