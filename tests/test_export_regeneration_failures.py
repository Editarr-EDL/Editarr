import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event

from scanner import repository
from scanner.db import (
    ExportRegenerationRow,
    MediaItemExportRow,
    SavedReviewRow,
    UserFilterPreferenceRow,
)
from scanner.persistence.repository.export_regeneration import (
    claim_regeneration,
    queue_regenerations,
)
from scanner.runtime.export_regeneration import ExportRegenerationScheduler, settle_regeneration
from tests.multi_user_access_support import ADMIN, USER
from tests.test_export_regeneration import publish_policy_review
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app
from tests.test_review_policy_view import draft, enforce
from tests.test_review_policy_view import policy_review as policy_review


def bump_policy(accounts):
    with accounts.factory() as db:
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        queue_regenerations(db)
        db.commit()


@pytest.mark.parametrize(
    ("poison", "outcome"),
    [
        ("media_removed", "withdrawn"),
        ("media_renamed", "withdrawn"),
        ("foreign_bytes", "withdrawn"),
        ("journal", "withdrawing"),
        ("symlink", "withdrawing"),
    ],
)
def test_poison_publication_does_not_starve_other_jobs(app, poison, outcome):
    first = publish(app, seeded_review(app, USER, "first"), "edl").json()
    second = publish(app, seeded_review(app, USER, "second"), "edl").json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        destination = Path(row.destination_path)
        media = Path(json.loads(row.snapshot_json)["media_path"])
    if poison == "media_removed":
        media.unlink()
    elif poison == "media_renamed":
        media.rename(media.with_suffix(".renamed.mkv"))
    elif poison == "foreign_bytes":
        destination.write_bytes(b"foreign bytes\n")
    elif poison == "symlink":
        destination.unlink()
        destination.symlink_to(media)
    else:
        destination.with_name(f".{destination.name}.editarr-recovery.json").write_text("{}")
    bump_policy(app.accounts)
    scheduler = ExportRegenerationScheduler(app.accounts.factory)
    for _ in range(4):
        scheduler.tick()
    with app.accounts.factory() as db:
        first_row = db.get(MediaItemExportRow, first["id"])
        assert first_row.regeneration.state == outcome
        assert (first_row.content_bytes is None) == (outcome == "withdrawn")
        second_row = db.get(MediaItemExportRow, second["id"])
        assert second_row.policy_revision == 1
        assert second_row.regeneration.state == "succeeded"
        assert Path(second_row.destination_path).read_bytes() == second_row.content_bytes
    if poison in {"media_removed", "media_renamed"}:
        assert not destination.exists()
    elif poison == "foreign_bytes":
        assert destination.read_bytes() == b"foreign bytes\n"
    elif poison == "symlink":
        assert destination.is_symlink()
        assert media.is_file()


def test_unrelated_default_journal_does_not_block_personal_work(app):
    shared = publish(
        app, seeded_review(app, USER, "shared"), "edl", user=ADMIN, target="item_default"
    ).json()
    personal = publish(app, seeded_review(app, USER, "personal"), "edl").json()
    with app.accounts.factory() as db:
        destination = Path(db.get(MediaItemExportRow, shared["id"]).destination_path)
    destination.with_name(f".{destination.name}.editarr-recovery.json").write_text("{}")
    bump_policy(app.accounts)
    assert ExportRegenerationScheduler(app.accounts.factory).tick() == "succeeded"
    with app.accounts.factory() as db:
        assert db.get(MediaItemExportRow, personal["id"]).policy_revision == 1


@pytest.mark.parametrize("live_edit", ["selected", "incomplete"])
@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_regeneration_keeps_published_review_and_sources(policy_review, live_edit, format):
    accounts, review_id = policy_review
    with accounts.factory() as db:
        db.get(SavedReviewRow, review_id).mcf_document.material_end_seconds = 100
        db.commit()
    published = publish_policy_review(accounts, review_id, format)
    with accounts.factory() as db:
        row = db.get(MediaItemExportRow, published["id"])
        before = json.loads(row.snapshot_json)
        exported_revision = row.exported_format_revision
        if live_edit == "selected":
            detail = repository.get_saved_review(db, review_id)
            edited = [dict(value, selected=True) for value in detail.model_dump()["filters"]]
            repository.save_saved_review(db, review_id, draft(detail, filters=edited))
        else:
            db.get(SavedReviewRow, review_id).mcf_document.material_end_seconds = None
            db.commit()
    enforce(accounts, rules=[], severities=["swearing"])
    with accounts.factory() as db:
        assert settle_regeneration(db, claim_regeneration(db)) == "succeeded"
        row = db.get(MediaItemExportRow, published["id"])
        after = json.loads(row.snapshot_json)
        assert row.exported_format_revision == exported_revision
        for key in ("sources", "review", "policy_evidence", "review_revision"):
            assert after[key] == before[key]
        assert row.content_bytes == Path(row.destination_path).read_bytes()
        assert b"00:00:01.000 --> 00:00:02.000" not in row.content_bytes


def test_idle_claim_statement_count_is_constant(app):
    counts = []
    for name in ("one", "two", "three"):
        review = seeded_review(app, USER, name)
        assert publish(app, review, "edl").status_code == 200
        assert publish(app, review, "mcf").status_code == 200
        statements = []

        def observe(_connection, _cursor, statement, *_args):
            statements.append(statement)

        event.listen(app.accounts.engine, "before_cursor_execute", observe)
        try:
            with app.accounts.factory() as db:
                assert claim_regeneration(db) is None
        finally:
            event.remove(app.accounts.engine, "before_cursor_execute", observe)
        counts.append(len(statements))
    assert counts[0] == counts[-1]


@pytest.mark.parametrize("boundary", ["lock", "prepare", "write"])
@pytest.mark.parametrize("failure", ["sqlite", "sqlalchemy", "maintenance"])
def test_transient_failure_keeps_old_bytes_for_retry(app, monkeypatch, boundary, failure):
    from sqlalchemy.exc import OperationalError

    from scanner.persistence.write_lock import write_lock
    from scanner.runtime import export_regeneration as worker
    from scanner.runtime.maintenance import MaintenanceUnavailable

    first = publish(app, seeded_review(app, USER, "busy"), "edl").json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        path, original = Path(row.destination_path), row.content_bytes
    bump_policy(app.accounts)
    with app.accounts.factory() as db:
        job = claim_regeneration(db)
    error = {
        "sqlite": sqlite3.OperationalError("database is locked"),
        "sqlalchemy": OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked")),
        "maintenance": MaintenanceUnavailable("maintenance is active"),
    }[failure]

    def fail(*_args, **_kwargs):
        raise error

    @contextmanager
    def busy_once(db):
        nonlocal error
        if error is not None:
            caught, error = error, None
            raise caught
        with write_lock(db):
            yield

    if boundary == "lock":
        monkeypatch.setattr(worker, "write_lock", busy_once)
    elif boundary == "prepare":
        monkeypatch.setattr(worker, "prepare_retained_export", fail)
    else:
        monkeypatch.setattr(worker, "recoverable_sidecar", fail)
    with app.accounts.factory() as db:
        assert settle_regeneration(db, job) == "retry"
        row = db.get(MediaItemExportRow, first["id"])
        assert row.content_bytes == path.read_bytes() == original
        assert row.regeneration.state == "running"


def test_scheduler_uses_configured_data_directory_for_external_database(app, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from scanner.runtime.maintenance import DatabaseMaintenance, MaintenanceUnavailable

    data_dir = tmp_path / "runtime-data"
    assert Path(app.accounts.engine.url.database).parent != data_dir
    scheduler = ExportRegenerationScheduler(app.accounts.factory, data_dir=data_dir)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with DatabaseMaintenance(data_dir).exclusive():
            with pytest.raises(MaintenanceUnavailable):
                executor.submit(scheduler.tick).result(timeout=5)
    assert scheduler.tick() is None


def test_delayed_claims_are_fair_and_attempts_are_capped(app, monkeypatch):
    from scanner.persistence.repository.export_regeneration import MAX_ATTEMPTS
    from scanner.runtime import export_regeneration as worker

    first = publish(app, seeded_review(app, USER, "retry-first"), "edl").json()
    second = publish(app, seeded_review(app, USER, "retry-second"), "edl").json()
    bump_policy(app.accounts)

    def busy(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    with app.accounts.factory() as db:
        first_job = claim_regeneration(db, now=1000)
        with monkeypatch.context() as injected:
            injected.setattr(worker, "prepare_retained_export", busy)
            assert settle_regeneration(db, first_job) == "retry"
        second_job = claim_regeneration(db, now=1000)
        assert second_job.export_id == second["id"]
        assert settle_regeneration(db, second_job) == "succeeded"
        assert claim_regeneration(db, now=1004) is None
        now = 1005
        for attempt in range(2, MAX_ATTEMPTS + 1):
            job = claim_regeneration(db, now=now)
            assert job.export_id == first["id"]
            with monkeypatch.context() as injected:
                injected.setattr(worker, "prepare_retained_export", busy)
                assert settle_regeneration(db, job) == "retry"
            job = db.get(ExportRegenerationRow, first["id"])
            assert job.attempts == attempt
            assert claim_regeneration(db, now=now) is None
            now = job.due_at
        assert claim_regeneration(db, now=now) is None
        row = db.get(MediaItemExportRow, first["id"])
        assert row.regeneration.state == "failed"
        assert row.regeneration.failure_reason == "retries_exhausted"
        assert row.regeneration.attempts == MAX_ATTEMPTS
        assert Path(row.destination_path).read_bytes() == row.content_bytes


def test_idle_scheduler_does_not_repeat_startup_sweep(app):
    scheduler = ExportRegenerationScheduler(app.accounts.factory)
    assert scheduler.tick() is None
    counts = []
    for name in ("idle-one", "idle-two", "idle-three"):
        assert publish(app, seeded_review(app, USER, name), "edl").status_code == 200
        statements = []

        def observe(_connection, _cursor, statement, *_args):
            statements.append(statement)

        event.listen(app.accounts.engine, "before_cursor_execute", observe)
        try:
            assert scheduler.tick() is None
        finally:
            event.remove(app.accounts.engine, "before_cursor_execute", observe)
        counts.append(len(statements))
    assert counts[0] == counts[-1]


def test_recovery_failure_settles_only_its_claim_without_touching_files(app, monkeypatch):
    from scanner.persistence.repository.export_recovery import ExportRecoveryRequired
    from scanner.runtime import export_regeneration as worker

    first = publish(app, seeded_review(app, USER, "uncertain-first"), "edl").json()
    second = publish(app, seeded_review(app, USER, "uncertain-second"), "edl").json()
    bump_policy(app.accounts)
    original = worker.recoverable_sidecar

    def interrupted(destination, *_args, **_kwargs):
        if destination == path:
            raise ExportRecoveryRequired("synthetic retained recovery file")
        return original(destination, *_args, **_kwargs)

    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        path, before = Path(row.destination_path), row.content_bytes
    monkeypatch.setattr(worker, "recoverable_sidecar", interrupted)
    scheduler = ExportRegenerationScheduler(app.accounts.factory)
    assert scheduler.tick() == "failed"
    assert path.read_bytes() == before
    assert scheduler.tick() == "succeeded"
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        assert row.regeneration.state == "withdrawing"
        assert row.content_bytes == before
        assert db.get(MediaItemExportRow, second["id"]).policy_revision == 1


def test_startup_sweep_reads_unlocked_and_rechecks_policy_before_queueing(app, monkeypatch):
    from scanner.persistence.repository import export_regeneration as jobs

    published = publish(app, seeded_review(app, USER, "sweep-race"), "edl").json()
    with app.accounts.factory() as db:
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        db.commit()
    original = jobs.policy_identity
    changed = False

    def change_between_discovery_and_queue(db, row):
        nonlocal changed
        identity = original(db, row)
        if not db.info.get("export_publication_lock") and not changed:
            assert not db.connection().connection.driver_connection.in_transaction
            with app.accounts.factory() as other:
                other.get(UserFilterPreferenceRow, USER).policy_revision = 2
                other.commit()
            changed = True
        return identity

    monkeypatch.setattr(jobs, "policy_identity", change_between_discovery_and_queue)
    with app.accounts.factory() as db:
        jobs.sweep_regenerations(db)
        job = claim_regeneration(db)
        assert changed
        assert job.export_id == published["id"]
        assert job.policy_revision == 2
        assert settle_regeneration(db, job) == "succeeded"
