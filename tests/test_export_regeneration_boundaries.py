"""Regeneration boundaries: retained paths, isolated probes, durable withdrawal, idle work."""

import json
import time
from pathlib import Path

import pytest
from sqlalchemy import event, text

from scanner.db import MediaItemExportRow
from scanner.persistence.repository.export_discovery import publication_status
from scanner.persistence.repository.export_recovery import _journal, digest
from scanner.persistence.repository.export_regeneration import (
    claim_regeneration,
    retry_regeneration,
)
from scanner.runtime.export_regeneration import ExportRegenerationScheduler, settle_regeneration
from tests.multi_user_access_support import ADMIN, USER
from tests.test_export_regeneration_failures import bump_policy
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app


def observe(app, export_id):
    """Return the public status, retained bytes and destination of one publication."""
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        return (
            publication_status(row, row.target_user_id),
            row.content_bytes,
            Path(row.destination_path),
        )


def later(hours=1):
    return time.time() + 3600 * hours


@pytest.mark.parametrize("media_present", [False, True])
def test_redirected_destination_is_refused_before_any_recovery(app, tmp_path, media_present):
    published = publish(app, seeded_review(app, USER, "redirected"), "edl").json()
    _status, original, retained = observe(app, published["id"])
    bump_policy(app.accounts)
    moved = app.media.with_name("original-media")
    foreign_root = tmp_path / "foreign-library"
    foreign_destination = foreign_root / retained.relative_to(app.media)
    foreign_destination.parent.mkdir(parents=True)
    if media_present:
        for media in app.media.glob("*.mkv"):
            (foreign_root / media.name).write_bytes(media.read_bytes())
    foreign = b"5 6 0\n"
    foreign_destination.write_bytes(foreign)
    marker = _journal(foreign_destination)
    marker.write_text(
        json.dumps({"version": 1, "old": digest(original), "new": digest(foreign), "backup": None})
    )
    journal = marker.read_bytes()
    app.media.rename(moved)
    app.media.symlink_to(foreign_root, target_is_directory=True)
    with app.accounts.factory() as db:
        assert settle_regeneration(db, claim_regeneration(db)) == "failed"
    assert foreign_destination.read_bytes() == foreign
    assert marker.read_bytes() == journal
    assert (moved / retained.relative_to(app.media)).read_bytes() == original
    status, retained_bytes, _destination = observe(app, published["id"])
    assert status["failed"] and status["serving"] and retained_bytes == original


@pytest.mark.parametrize("target", ["self", "item_default"])
def test_journal_probe_failure_is_isolated_to_its_publication(app, monkeypatch, target):
    poisoned = publish(
        app, seeded_review(app, USER, "inaccessible"), "edl", user=ADMIN, target=target
    ).json()
    healthy = publish(app, seeded_review(app, USER, "healthy"), "edl").json()
    marker = _journal(observe(app, poisoned["id"])[2])
    bump_policy(app.accounts)
    original_stat = Path.stat

    def unavailable(path, *args, **kwargs):
        if path == marker:
            raise PermissionError("synthetic inaccessible sidecar directory")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", unavailable)
    scheduler = ExportRegenerationScheduler(app.accounts.factory)
    outcomes = [scheduler.tick() for _attempt in range(3)]
    status, content, destination = observe(app, healthy["id"])
    assert "succeeded" in outcomes
    assert status["regeneration_state"] == "succeeded" and status["policy_revision"] == 1
    assert destination.read_bytes() == content


def test_failed_withdrawal_keeps_ownership_until_removal_is_durable(app, monkeypatch):
    published = publish(app, seeded_review(app, USER, "unlink-denied"), "edl").json()
    _status, original, destination = observe(app, published["id"])
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, published["id"])
        Path(json.loads(row.snapshot_json)["media_path"]).unlink()
    bump_policy(app.accounts)
    original_unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path == destination:
            raise PermissionError("synthetic unlink denial")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as injected:
        injected.setattr(Path, "unlink", denied)
        with app.accounts.factory() as db:
            assert settle_regeneration(db, claim_regeneration(db)) == "failed"
        with app.accounts.factory() as db:
            assert settle_regeneration(db, claim_regeneration(db, now=later())) == "failed"
    status, retained, _destination = observe(app, published["id"])
    assert status["failed"] and status["retryable"] and status["serving"]
    assert retained == original == destination.read_bytes()
    with app.accounts.factory() as db:
        assert settle_regeneration(db, claim_regeneration(db, now=later(2))) == "failed"
    status, retained, _destination = observe(app, published["id"])
    assert not destination.exists()
    assert status["failed"] and not status["serving"] and retained is None
    with app.accounts.factory() as db:
        assert claim_regeneration(db, now=later(3)) is None


def test_explicit_retry_after_denied_unlink_withdraws_owned_bytes(app, monkeypatch):
    published = publish(app, seeded_review(app, USER, "retry-unlink"), "edl").json()
    _status, _original, destination = observe(app, published["id"])
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, published["id"])
        Path(json.loads(row.snapshot_json)["media_path"]).unlink()
    bump_policy(app.accounts)
    original_unlink = Path.unlink

    def denied(path, *args, **kwargs):
        if path == destination:
            raise PermissionError("synthetic unlink denial")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as injected:
        injected.setattr(Path, "unlink", denied)
        with app.accounts.factory() as db:
            assert settle_regeneration(db, claim_regeneration(db)) == "failed"
    status = observe(app, published["id"])[0]
    with app.accounts.factory() as db:
        assert retry_regeneration(db, published["id"], status["publication_id"])
        assert settle_regeneration(db, claim_regeneration(db)) == "failed"
    assert not destination.exists()
    assert not observe(app, published["id"])[0]["serving"]


def test_readonly_destination_regenerates_after_write_access_returns(app, monkeypatch):
    from scanner.persistence.repository import export_recovery

    published = publish(app, seeded_review(app, USER, "readonly-live"), "edl").json()
    _status, original, destination = observe(app, published["id"])
    bump_policy(app.accounts)
    original_temporary = export_recovery._temporary

    def readonly(path, *args, **kwargs):
        if path == destination:
            raise PermissionError("synthetic read-only sidecar directory")
        return original_temporary(path, *args, **kwargs)

    with monkeypatch.context() as injected:
        injected.setattr(export_recovery, "_temporary", readonly)
        with app.accounts.factory() as db:
            assert settle_regeneration(db, claim_regeneration(db)) == "failed"
    status, retained, _destination = observe(app, published["id"])
    assert status["failed"] and status["serving"]
    assert retained == original == destination.read_bytes()
    with app.accounts.factory() as db:
        assert retry_regeneration(db, published["id"], status["publication_id"])
        assert settle_regeneration(db, claim_regeneration(db)) == "succeeded"
    status, content, _destination = observe(app, published["id"])
    assert status["regeneration_state"] == "succeeded" and status["policy_revision"] == 1
    assert destination.read_bytes() == content


def _copy_publication(db, export_id, copies, label):
    """Clone a settled publication and every row that references it, whatever its tables."""
    connection = db.connection()

    def columns(table):
        return list(connection.exec_driver_sql(f'PRAGMA table_info("{table}")'))

    exported = [column[1] for column in columns("media_item_exports") if column[1] != "id"]
    selection = ", ".join(
        f"'{label}-' || :copy"
        if name == "media_source_id"
        else f"destination_path || '.{label}-' || :copy"
        if name == "destination_path"
        else name
        for name in exported
    )
    children = [
        (table, reference[3])
        for (table,) in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        for reference in connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")')
        if reference[2] == "media_item_exports"
    ]
    for copy in range(copies):
        copied = connection.execute(
            text(
                f"INSERT INTO media_item_exports ({', '.join(exported)}) "
                f"SELECT {selection} FROM media_item_exports WHERE id = :id"
            ),
            {"copy": str(copy), "id": export_id},
        ).lastrowid
        for table, reference in children:
            names = [c[1] for c in columns(table) if not c[5] or c[1] == reference]
            chosen = ", ".join(":copied" if name == reference else name for name in names)
            connection.execute(
                text(
                    f"INSERT INTO {table} ({', '.join(names)}) "
                    f"SELECT {chosen} FROM {table} WHERE {reference} = :id"
                ),
                {"copied": copied, "id": export_id},
            )


def test_idle_claim_work_is_indexed_and_independent_of_publication_count(app):
    published = publish(app, seeded_review(app, USER, "idle-work"), "edl").json()
    bump_policy(app.accounts)
    with app.accounts.factory() as db:
        assert settle_regeneration(db, claim_regeneration(db)) == "succeeded"
    instructions = 0
    selects = []

    def progress():
        nonlocal instructions
        instructions += 1
        return 0

    def before(connection, _cursor, statement, parameters, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append((statement, parameters))
        connection.connection.driver_connection.set_progress_handler(progress, 1)

    def after(connection, *_args):
        connection.connection.driver_connection.set_progress_handler(None, 0)

    counts = []
    for copies in (0, 100, 1000):
        with app.accounts.factory() as db:
            _copy_publication(db, published["id"], copies, f"idle{copies}")
            db.commit()
        event.listen(app.accounts.engine, "before_cursor_execute", before)
        event.listen(app.accounts.engine, "after_cursor_execute", after)
        try:
            instructions = 0
            selects.clear()
            with app.accounts.factory() as db:
                assert claim_regeneration(db) is None
            counts.append(instructions)
        finally:
            event.remove(app.accounts.engine, "before_cursor_execute", before)
            event.remove(app.accounts.engine, "after_cursor_execute", after)
    assert counts[0] > 0 and counts[0] == counts[1] == counts[2], counts
    assert selects
    with app.accounts.engine.connect() as connection:
        for statement, parameters in selects:
            plan = [
                row[3]
                for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters)
            ]
            assert not any(step.startswith("SCAN") or "TEMP B-TREE" in step for step in plan), (
                statement,
                plan,
            )
