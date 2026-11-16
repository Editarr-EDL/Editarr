"""Regeneration converges after unknown filesystem states and policy identities that return."""

import json
import time
from pathlib import Path

import pytest

from scanner.db import MediaItemExportRow, SavedReviewRow, ScanRow, UserFilterPreferenceRow
from scanner.persistence.repository.export_regeneration import (
    claim_regeneration,
    queue_regenerations,
    retry_regeneration,
    sweep_regenerations,
)
from scanner.runtime.export_regeneration import settle_regeneration
from tests.multi_user_access_support import PEER_USER, USER
from tests.test_export_regeneration_boundaries import observe
from tests.test_export_regeneration_failures import bump_policy
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app


def later(hours):
    return time.time() + 3600 * hours


def settle_due(app, hours):
    with app.accounts.factory() as db:
        job = claim_regeneration(db, now=later(hours))
        return None if job is None else settle_regeneration(db, job)


def job_state(app, export_id):
    with app.accounts.factory() as db:
        job = db.get(MediaItemExportRow, export_id).regeneration
        return None if job is None else (job.state, job.policy_owner_id, job.policy_revision)


def set_policy_revision(app, revision):
    """Change the stored policy without queueing, as a settings restore does."""
    with app.accounts.factory() as db:
        db.get(UserFilterPreferenceRow, USER).policy_revision = revision
        db.commit()


def nested_review(app, owner, name):
    """Seed a review whose media lives at share/<name>/, so the share can vanish alone."""
    review = seeded_review(app, owner, name)
    with app.accounts.factory() as db:
        media_item_id = db.get(SavedReviewRow, review.id).media_item_id
        scans = db.query(ScanRow).filter(ScanRow.media_item_id == media_item_id).all()
        old = Path(scans[0].source_path)
        new = app.media / "share" / name / old.name
        new.parent.mkdir(parents=True)
        old.rename(new)
        for scan in scans:
            scan.source_path = scan.resolved_local_path = str(new)
        scans[0].media_item.path = str(new)
        item = app.items[scans[0].media_item.jellyfin_id]
        app.items[item.id] = item.model_copy(update={"path": str(new)})
        db.commit()
    return review


def unmount(app):
    share = app.media / "share"
    away = share.with_name("share-unmounted")
    share.rename(away)
    return away


@pytest.mark.parametrize("export_format", ["edl", "mcf"])
@pytest.mark.parametrize("media_returns", [True, False])
def test_unavailable_library_keeps_evidence_until_it_returns(app, media_returns, export_format):
    review = nested_review(app, USER, "unmounted")
    published = publish(app, review, export_format).json()
    _status, original, destination = observe(app, published["id"])
    bump_policy(app.accounts)
    away = unmount(app)
    try:
        assert settle_due(app, 0) == "failed"
        assert settle_due(app, 1) is None
        status, retained, _destination = observe(app, published["id"])
        assert status["failed"] and status["retryable"] and not status["serving"]
        assert retained is None
        assert job_state(app, published["id"]) == ("withdrawn", USER, 1)
    finally:
        away.rename(app.media / "share")
    assert destination.read_bytes() == original
    if not media_returns:
        with app.accounts.factory() as db:
            row = db.get(MediaItemExportRow, published["id"])
            media = Path(json.loads(row.snapshot_json)["media_path"])
        media.unlink()
    with app.accounts.factory() as db:
        sweep_regenerations(db)
    assert settle_due(app, 2) == ("succeeded" if media_returns else "failed")
    status, retained, _destination = observe(app, published["id"])
    if media_returns:
        assert status["regeneration_state"] == "succeeded" and status["policy_revision"] == 1
        assert destination.read_bytes() == retained
        replaced = publish(app, review, export_format, policy_owner_id=USER, policy_revision=1)
        assert replaced.status_code == 200, replaced.text
    else:
        assert job_state(app, published["id"])[0] == "withdrawn"
        assert not status["serving"] and retained is None and not destination.exists()
    assert settle_due(app, 3) is None


def test_owner_round_trip_before_the_worker_runs_leaves_the_file_current(app):
    review = seeded_review(app, USER, "round-trip")
    published = publish(app, review, "edl").json()
    _status, original, destination = observe(app, published["id"])
    for owner in (PEER_USER, USER):
        with app.accounts.factory() as db:
            db.get(SavedReviewRow, review.id).owner_id = owner
            queue_regenerations(db, review_ids=[review.id])
            db.commit()
    assert job_state(app, published["id"]) is None
    assert [settle_due(app, hours) for hours in range(6)] == [None] * 6
    status, retained, _destination = observe(app, published["id"])
    assert not status["failed"] and status["regeneration_state"] == "idle"
    assert retained == original == destination.read_bytes()


def test_restored_published_policy_drops_pending_work_at_the_next_claim(app):
    published = publish(app, seeded_review(app, USER, "restored"), "edl").json()
    _status, original, destination = observe(app, published["id"])
    bump_policy(app.accounts)
    set_policy_revision(app, 0)
    with app.accounts.factory() as db:
        sweep_regenerations(db)
    assert [settle_due(app, hours) for hours in range(6)] == [None] * 6
    status, retained, _destination = observe(app, published["id"])
    assert not status["failed"] and status["regeneration_state"] == "idle"
    assert retained == original == destination.read_bytes()


def test_restored_published_policy_ends_an_unfinished_withdrawal(app):
    published = publish(app, nested_review(app, USER, "withdrawing"), "edl").json()
    _status, original, destination = observe(app, published["id"])
    bump_policy(app.accounts)
    item = app.media / "share" / "withdrawing"
    item.chmod(0)
    try:
        assert settle_due(app, 0) == "failed"
        assert job_state(app, published["id"])[0] == "withdrawing"
    finally:
        item.chmod(0o755)
    set_policy_revision(app, 0)
    assert settle_due(app, 1) is None
    assert job_state(app, published["id"]) is None
    status, retained, _destination = observe(app, published["id"])
    assert status["serving"] and not status["failed"]
    assert retained == original == destination.read_bytes()


def test_restored_published_policy_keeps_a_withdrawn_file_withdrawn(app):
    published = publish(app, seeded_review(app, USER, "withdrawn"), "edl").json()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, published["id"])
        media = Path(json.loads(row.snapshot_json)["media_path"])
    media.unlink()
    bump_policy(app.accounts)
    assert settle_due(app, 0) == "failed"
    set_policy_revision(app, 2)
    with app.accounts.factory() as db:
        queue_regenerations(db)
        db.commit()
    set_policy_revision(app, 0)
    assert settle_due(app, 1) is None
    status, retained, destination = observe(app, published["id"])
    assert job_state(app, published["id"])[0] == "withdrawn"
    assert status["failed"] and status["retryable"] and not status["serving"]
    assert retained is None and not destination.exists()


def test_explicit_retry_targets_the_current_policy(app):
    published = publish(app, nested_review(app, USER, "retry-current"), "edl").json()
    bump_policy(app.accounts)
    away = unmount(app)
    try:
        assert settle_due(app, 0) == "failed"
    finally:
        away.rename(app.media / "share")
    set_policy_revision(app, 2)
    status = observe(app, published["id"])[0]
    with app.accounts.factory() as db:
        assert retry_regeneration(db, published["id"], status["publication_id"])
    assert job_state(app, published["id"]) == ("queued", USER, 2)
    assert settle_due(app, 0) == "succeeded"
    status, retained, destination = observe(app, published["id"])
    assert status["regeneration_state"] == "succeeded" and status["policy_revision"] == 2
    assert destination.read_bytes() == retained
