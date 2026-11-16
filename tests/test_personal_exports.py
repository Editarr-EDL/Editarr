import sqlite3
import subprocess
import sys

import pytest

from scanner.formats.export_layout import LocalExportId
from scanner.models import ExportFormat
from scanner.persistence.repository.export_publication import safe_destination


@pytest.mark.parametrize("export_format", list(ExportFormat))
def test_canonical_destination_preserves_legacy_and_rejects_redirection(tmp_path, export_format):
    media = tmp_path / "Movie.mkv"
    media.write_bytes(b"synthetic media")
    legacy = media.with_suffix(f".{export_format.value}")
    legacy.write_bytes(b"preserved legacy")
    target = LocalExportId("local-" + "1" * 32)
    expected = (
        tmp_path
        / export_format.value
        / target.value
        / (f"Movie.user-{target.value}.{export_format.value}")
    )
    assert safe_destination(media, export_format, target) == expected
    assert safe_destination(media, export_format, None) == (
        tmp_path / export_format.value / f"Movie.{export_format.value}"
    )
    assert legacy.read_bytes() == b"preserved legacy"

    outside = tmp_path / "outside"
    outside.mkdir()
    format_directory = tmp_path / export_format.value
    format_directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="[Ss]ymbolic|[Ss]ymlink"):
        safe_destination(media, export_format, target)
    format_directory.unlink()
    expected.parent.mkdir(parents=True)
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"untouched")
    expected.symlink_to(sentinel)
    with pytest.raises(ValueError, match="[Ss]ymbolic|[Ss]ymlink"):
        safe_destination(media, export_format, target)
    assert sentinel.read_bytes() == b"untouched"
    assert legacy.read_bytes() == b"preserved legacy"


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("deleting", [False, True])
def test_hard_exit_reconciles_sidecar_to_committed_snapshot(tmp_path, format, committed, deleting):
    from scanner.persistence.repository.export_recovery import recover_sidecar

    destination = tmp_path / f"synthetic.{format}"
    destination.write_bytes(b"old complete content")
    database = tmp_path / "snapshot.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE snapshot (content BLOB)")
        connection.execute("INSERT INTO snapshot VALUES (?)", (destination.read_bytes(),))
    script = """
import os, sqlite3, sys
from pathlib import Path
from scanner.persistence.repository.export_recovery import recoverable_sidecar
destination, database, committed, deleting = sys.argv[1:]
connection = sqlite3.connect(database)
connection.execute("BEGIN IMMEDIATE")
content = None if deleting == "True" else b"new complete content"
with recoverable_sidecar(Path(destination), content, journaled=True):
    connection.execute("UPDATE snapshot SET content = ?", (content,))
    if committed == "True":
        connection.commit()
    os._exit(73)
"""
    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(destination),
            str(database),
            str(committed),
            str(deleting),
        ],
        capture_output=True,
        check=False,
    )
    assert crashed.returncode == 73, crashed.stderr.decode()
    assert list(tmp_path.glob(".*.editarr-recovery.json"))
    with sqlite3.connect(database) as connection:
        content = connection.execute("SELECT content FROM snapshot").fetchone()[0]
    recover_sidecar(destination, content)
    assert (destination.read_bytes() if destination.exists() else None) == content
    assert not list(tmp_path.glob(".*.bak"))
    assert not list(tmp_path.glob(".*.editarr-recovery.json"))


def test_publication_lock_survives_commit_and_serializes_cleanup(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from scanner.persistence.repository.export_recovery import publication_lock

    engine = create_engine(f"sqlite:///{tmp_path / 'publication.db'}")
    started, finished = Event(), Event()

    def next_writer():
        with Session(engine) as db:
            started.set()
            with publication_lock(db):
                finished.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor, Session(engine) as db:
            with publication_lock(db):
                db.execute(text("CREATE TABLE durable (id INTEGER)"))
                db.commit()
                future = executor.submit(next_writer)
                assert started.wait(timeout=5)
                assert not finished.wait(timeout=0.1)
            assert finished.wait(timeout=5)
            future.result(timeout=5)
    finally:
        engine.dispose()
