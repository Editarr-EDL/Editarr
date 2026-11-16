import json
import time
from pathlib import Path

import pytest

from scanner.db import MediaItemExportRow, UserFilterPreferenceRow
from scanner.persistence.repository.export_regeneration import queue_regenerations
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app
from tests.test_review_policy_view import policy_review as policy_review


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_policy_job_is_durable_idempotent_and_uses_source_owner(app, format):
    from scanner.persistence.repository.export_regeneration import queue_regenerations

    review = seeded_review(app, USER, "regenerate")
    response = publish(app, review, format, user=ADMIN)
    assert response.status_code == 200, response.text
    export_id = response.json()["id"]
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        before = row.content_bytes
        destination = Path(row.destination_path)
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        db.commit()
        assert queue_regenerations(db) == 1
        db.commit()
    with app.accounts.factory() as db:
        assert queue_regenerations(db) == 0
        row = db.get(MediaItemExportRow, export_id)
        job = row.regeneration
        assert job.state == "queued"
        assert job.policy_owner_id == USER
        assert job.policy_revision == 1
        assert row.target_user_id == ADMIN
        assert destination.read_bytes() == before == row.content_bytes


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_claim_restart_duplicate_delivery_and_retained_review(app, format):
    from scanner.db import AuditEventRow, SavedReviewRow
    from scanner.persistence.repository.export_regeneration import claim_regeneration
    from scanner.runtime.export_regeneration import settle_regeneration

    review = seeded_review(app, USER, "restart")
    response = publish(app, review, format)
    assert response.status_code == 200, response.text
    export_id = response.json()["id"]
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        row.review_id = row.exported_format_revision = None
        db.flush()
        db.delete(db.get(SavedReviewRow, review.id))
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        queue_regenerations(db)
        db.commit()
        old = claim_regeneration(db)
    with app.accounts.factory() as db:
        current = claim_regeneration(db, now=time.time() + 10)
        assert current.token != old.token
        assert settle_regeneration(db, old) == "stale"
        assert settle_regeneration(db, current) == "succeeded"
        assert settle_regeneration(db, current) == "stale"
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        assert row.policy_revision == 1
        assert Path(row.destination_path).read_bytes() == row.content_bytes
        assert json.loads(row.snapshot_json)["source_review_id"] == review.id
        assert db.query(MediaItemExportRow).count() == 1
        assert db.query(AuditEventRow).filter_by(action="export.regenerate").count() == 1


def publish_policy_review(accounts, review_id, format):
    from scanner.models import ExportFormat
    from scanner.persistence.repository.export_publication import publish_export
    from scanner.persistence.repository.export_recovery import publication_lock
    from scanner.security.access.vocabulary import Role
    from tests.multi_user_access_support import build_access

    with accounts.factory() as db, publication_lock(db):
        result = publish_export(
            db, review_id, 0, ExportFormat(format), access=build_access(Role.USER)
        )
        assert result is not None
        return result


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("retained", [False, True])
def test_enforced_replacement_matches_live_download_and_preserves_source(
    policy_review, format, retained
):
    from scanner import repository
    from scanner.db import AuditEventRow, SavedReviewRow
    from scanner.models import ExportFormat
    from scanner.persistence.repository.export_regeneration import claim_regeneration
    from scanner.persistence.repository.review_export_content import prepare_review_export
    from scanner.runtime.export_regeneration import settle_regeneration
    from tests.test_review_policy_view import base_rows, enforce

    accounts, review_id = policy_review
    with accounts.factory() as db:
        db.get(SavedReviewRow, review_id).mcf_document.material_end_seconds = 100
        db.commit()
    result = publish_policy_review(accounts, review_id, format)
    before_base = base_rows(accounts, review_id)
    with accounts.factory() as db:
        row = db.get(MediaItemExportRow, result["id"])
        old_bytes = row.content_bytes
        path = Path(row.destination_path)
        snapshot = json.loads(row.snapshot_json)
    enforce(accounts, severities=["swearing"])
    with accounts.factory() as db:
        job = claim_regeneration(db)
        assert path.read_bytes() == old_bytes
        detail = repository.get_saved_review(db, review_id)
        expected = prepare_review_export(
            db,
            review_id,
            detail.revision,
            ExportFormat(format),
            policy_revision=detail.policy_revision,
            policy_owner_id=USER,
        ).content
        db.rollback()
        assert expected != old_bytes
        if retained:
            row = db.get(MediaItemExportRow, result["id"])
            row.review_id = row.exported_format_revision = None
            db.flush()
            db.delete(db.get(SavedReviewRow, review_id))
            db.commit()
        assert settle_regeneration(db, job) == "succeeded"
    with accounts.factory() as db:
        row = db.get(MediaItemExportRow, result["id"])
        assert path.read_bytes() == row.content_bytes == expected
        assert row.policy_revision == job.policy_revision
        assert row.actor_id == USER
        after = json.loads(row.snapshot_json)
        assert after["source_review_id"] == snapshot["source_review_id"]
        assert after["sources"] == snapshot["sources"]
        event = db.query(AuditEventRow).filter_by(action="export.regenerate").one()
        assert event.actor_kind == "system"
        assert event.outcome == "succeeded"
        assert json.loads(event.details_json)["policy_revision"] == job.policy_revision
    if not retained:
        assert base_rows(accounts, review_id) == before_base


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("fault", ["prepare", "write"])
def test_confirmed_failure_withdraws_only_personal_and_explicit_retry_recovers(
    app, monkeypatch, format, fault
):
    from scanner.db import AuditEventRow
    from scanner.persistence.repository import export_recovery
    from scanner.persistence.repository.export_regeneration import (
        claim_regeneration,
        retry_regeneration,
    )
    from scanner.runtime import export_regeneration as worker

    review = seeded_review(app, USER, "failed")
    personal = publish(app, review, format).json()
    default = publish(app, review, format, user=ADMIN, target="item_default").json()
    with app.accounts.factory() as db:
        path = Path(db.get(MediaItemExportRow, personal["id"]).destination_path)
        fallback = db.get(MediaItemExportRow, default["id"])
        fallback_path, fallback_bytes = Path(fallback.destination_path), fallback.content_bytes
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        queue_regenerations(db)
        db.commit()
        job = claim_regeneration(db)
    original = export_recovery._temporary

    def write_failed(destination, content, suffix):
        if suffix == ".tmp":
            raise OSError("synthetic private path must not reach status")
        return original(destination, content, suffix)

    with monkeypatch.context() as injected:
        if fault == "prepare":
            injected.setattr(
                worker,
                "prepare_retained_export",
                lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("private source detail")),
            )
        else:
            injected.setattr(export_recovery, "_temporary", write_failed)
        with app.accounts.factory() as db:
            assert worker.settle_regeneration(db, job) == "failed"
    assert not path.exists()
    assert fallback_path.read_bytes() == fallback_bytes
    response = app.send("GET", f"/api/exports/{personal['id']}", app.accounts.cookie(USER))
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["failed"] and state["outdated"] and state["retryable"]
    assert not state["serving"] and state["content_sha256"] is None
    assert state["failure_reason"] == "write_failed"
    assert "private" not in response.text
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, personal["id"])
        assert row.content_bytes is None
        assert json.loads(row.snapshot_json)["source_review_id"] == review.id
        event = db.query(AuditEventRow).filter_by(action="export.regenerate").one()
        assert event.outcome == "failed"
        assert json.loads(event.details_json)["reason"] == "write_failed"
        assert retry_regeneration(db, row.id, state["publication_id"])
        retry = claim_regeneration(db)
        assert worker.settle_regeneration(db, retry) == "succeeded"
    assert path.exists()
    assert fallback_path.read_bytes() == fallback_bytes


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("change", ["policy", "replace", "delete", "reuse", "owner"])
def test_paused_worker_cannot_overwrite_newer_policy_publication_or_withdrawal(app, format, change):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from scanner.db import AuditEventRow, SavedReviewRow
    from scanner.persistence.repository.export_regeneration import claim_regeneration
    from scanner.runtime.export_regeneration import settle_regeneration
    from tests.multi_user_access_support import PEER_USER

    review = seeded_review(app, USER, "stale")
    first = publish(app, review, format).json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        path = Path(row.destination_path)
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        queue_regenerations(db)
        db.commit()
        job = claim_regeneration(db)
    paused, resume = Event(), Event()

    def worker():
        paused.set()
        assert resume.wait(10)
        with app.accounts.factory() as db:
            return settle_regeneration(db, job)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker)
        assert paused.wait(5)
        if change == "policy":
            with app.accounts.factory() as db:
                db.get(UserFilterPreferenceRow, USER).policy_revision = 2
                queue_regenerations(db)
                db.commit()
        elif change == "owner":
            with app.accounts.factory() as db:
                db.get(SavedReviewRow, review.id).owner_id = PEER_USER
                queue_regenerations(db, review_ids=[review.id])
                db.commit()
        elif change == "replace":
            response = publish(app, review, format, policy_owner_id=USER, policy_revision=1)
            assert response.status_code == 200, response.text
        else:
            removed = app.send(
                "DELETE",
                f"/api/exports/{first['id']}?revision=1&publication_id={first['publication_id']}",
                app.accounts.unsafe_cookie(USER),
            )
            assert removed.status_code == 204, removed.text
            if change == "reuse":
                peer = seeded_review(app, PEER_USER, "reused")
                response = publish(app, peer, format, user=PEER_USER)
                assert response.status_code == 200, response.text
                assert response.json()["id"] == first["id"]
        before = path.read_bytes() if path.exists() else None
        resume.set()
        assert future.result(10) == "stale"
    assert (path.read_bytes() if path.exists() else None) == before
    with app.accounts.factory() as db:
        assert db.query(AuditEventRow).filter_by(action="export.regenerate").count() == 0
        if change in {"policy", "owner"}:
            current = claim_regeneration(db)
            assert settle_regeneration(db, current) == "succeeded"
            row = db.get(MediaItemExportRow, first["id"])
            assert row.policy_revision == (2 if change == "policy" else 0)
            assert row.policy_owner_id == (USER if change == "policy" else PEER_USER)


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_audit_failure_preserves_old_file_and_restart_recovers(app, format):
    from sqlalchemy import text

    from scanner.db import AuditEventRow
    from scanner.persistence.repository.export_regeneration import claim_regeneration
    from scanner.runtime.export_regeneration import settle_regeneration

    review = seeded_review(app, USER, "audit-failure")
    first = publish(app, review, format).json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        path, old = Path(row.destination_path), row.content_bytes
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        queue_regenerations(db)
        db.commit()
        job = claim_regeneration(db)
        db.execute(
            text(
                "CREATE TRIGGER fail_regeneration BEFORE INSERT ON audit_events "
                "WHEN NEW.action = 'export.regenerate' "
                "BEGIN SELECT RAISE(ABORT, 'injected'); END"
            )
        )
        db.commit()
        with pytest.raises(Exception, match="injected"):
            settle_regeneration(db, job)
    assert path.read_bytes() == old
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        assert row.content_bytes == old
        assert row.publication_revision == 1
        assert row.regeneration.state == "running"
        assert db.query(AuditEventRow).filter_by(action="export.regenerate").count() == 0
        db.execute(text("DROP TRIGGER fail_regeneration"))
        db.commit()
        recovered = claim_regeneration(db, now=time.time() + 10)
        assert settle_regeneration(db, recovered) == "succeeded"
    assert not list(path.parent.glob(".*.editarr-recovery.json"))
    assert not list(path.parent.glob(".*.bak"))


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_cancelled_worker_does_not_settle_and_new_scheduler_resumes(app, monkeypatch, format):
    from scanner.persistence.repository.export_regeneration import claim_regeneration
    from scanner.runtime import export_regeneration as worker

    review = seeded_review(app, USER, "shutdown")
    first = publish(app, review, format).json()
    with app.accounts.factory() as db:
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        queue_regenerations(db)
        db.commit()
        job = claim_regeneration(db)
    scheduler = worker.ExportRegenerationScheduler(app.accounts.factory)
    original = worker.prepare_retained_export

    def stopping(*args, **kwargs):
        output = original(*args, **kwargs)
        scheduler.stop()
        return output

    with monkeypatch.context() as injected:
        injected.setattr(worker, "prepare_retained_export", stopping)
        with app.accounts.factory() as db:
            assert worker.settle_regeneration(db, job, cancelled=scheduler.stop_event) == "stale"
            assert db.get(MediaItemExportRow, first["id"]).publication_revision == 1
    assert scheduler.tick() is None
    restarted = worker.ExportRegenerationScheduler(app.accounts.factory)
    resumed_at = time.time() + 10
    with monkeypatch.context() as injected:
        injected.setattr(
            "scanner.persistence.repository.export_regeneration.time.time", lambda: resumed_at
        )
        assert restarted.tick() == "succeeded"
    assert restarted.tick() is None


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_unverifiable_deleted_snapshot_fails_closed(app, format):
    from scanner.db import SavedReviewRow
    from scanner.persistence.repository.export_regeneration import claim_regeneration
    from scanner.runtime.export_regeneration import settle_regeneration

    review = seeded_review(app, USER, "unverifiable")
    first = publish(app, review, format).json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        path = Path(row.destination_path)
        snapshot = json.loads(row.snapshot_json)
        del snapshot["review"]["base_filters"]
        row.snapshot_json = json.dumps(snapshot)
        row.review_id = row.exported_format_revision = None
        db.flush()
        db.delete(db.get(SavedReviewRow, review.id))
        db.get_one(UserFilterPreferenceRow, USER).policy_revision = 1
        queue_regenerations(db)
        db.commit()
        job = claim_regeneration(db)
        assert settle_regeneration(db, job) == "failed"
        row = db.get(MediaItemExportRow, first["id"])
        assert row.content_bytes is None
        assert row.regeneration.state == "withdrawn"
        assert row.regeneration.failure_reason == "regeneration_failed"
    assert not path.exists()
