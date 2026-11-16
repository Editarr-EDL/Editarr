"""Withdrawal keeps ownership evidence, so an absence that was only an outage stays recoverable."""

import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from scanner.db import AuditEventRow, MediaItemExportRow, SavedReviewRow, ScanRow
from scanner.persistence.repository.export_regeneration import (
    retry_regeneration,
    sweep_regenerations,
)
from scanner.security.audit.actions import AuditEventRejected
from tests.multi_user_access_support import USER
from tests.test_export_regeneration_boundaries import observe
from tests.test_export_regeneration_convergence import job_state, settle_due
from tests.test_export_regeneration_failures import bump_policy
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app

FORMATS = pytest.mark.parametrize("export_format", ["edl", "mcf"])
HDIUTIL = shutil.which("hdiutil")


def relocate(app, review, relative):
    """Move the seeded media to app.media/<relative> and point every record at it."""
    with app.accounts.factory() as db:
        media_item_id = db.get(SavedReviewRow, review.id).media_item_id
        scans = db.query(ScanRow).filter(ScanRow.media_item_id == media_item_id).all()
        old = Path(scans[0].source_path)
        new = app.media / relative / old.name
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(old, new)
        for scan in scans:
            scan.source_path = scan.resolved_local_path = str(new)
        scans[0].media_item.path = str(new)
        item = app.items[scans[0].media_item.jellyfin_id]
        app.items[item.id] = item.model_copy(update={"path": str(new)})
        db.commit()
    return new


@contextmanager
def disk_image(tmp_path, mountpoint):
    """Attach a real HFS+ image at `mountpoint`; yield attach and detach, always detaching."""
    image = tmp_path / "share.dmg"
    subprocess.run(
        [HDIUTIL, "create", "-quiet", "-size", "16m", "-fs", "HFS+", "-volname", "mu34", image],
        check=True,
    )
    mountpoint.mkdir(parents=True, exist_ok=True)
    attached = []

    def attach():
        subprocess.run(
            [HDIUTIL, "attach", "-quiet", "-nobrowse", "-mountpoint", mountpoint, image],
            check=True,
        )
        attached.append(True)

    def detach():
        subprocess.run([HDIUTIL, "detach", "-quiet", mountpoint], check=True)
        attached.clear()

    attach()
    try:
        yield attach, detach
    finally:
        if attached:
            detach()


def sweep(app):
    with app.accounts.factory() as db:
        sweep_regenerations(db)


def delete(app, export_id):
    status = observe(app, export_id)[0]
    return app.send(
        "DELETE",
        f"/api/exports/{export_id}?revision={status['publication_revision']}"
        f"&publication_id={status['publication_id']}",
        app.accounts.unsafe_cookie(USER),
    )


def regeneration_reasons(app, export_id):
    with app.accounts.factory() as db:
        events = db.query(AuditEventRow).filter_by(
            action="export.regenerate", resource=f"export:{export_id}"
        )
        return [json.loads(event.details_json).get("reason") for event in events]


def assert_withdrawn_as_absent(app, export_id):
    status, retained, _destination = observe(app, export_id)
    assert job_state(app, export_id)[0] == "withdrawn"
    assert status["failed"] and status["retryable"] and not status["serving"]
    assert status["failure_reason"] == "write_failed" and retained is None
    assert regeneration_reasons(app, export_id)[-1] == "retained_path_absent"


def assert_converges_after_return(app, review, export_format, export_id, destination, original):
    """The sweep re-queues returned owned bytes; replacement, republish, and delete all work."""
    assert destination.read_bytes() == original
    assert settle_due(app, 1) is None
    sweep(app)
    assert job_state(app, export_id)[0] == "queued"
    assert settle_due(app, 1) == "succeeded"
    status, retained, _destination = observe(app, export_id)
    assert status["serving"] and status["regeneration_state"] == "succeeded"
    assert status["policy_revision"] == 1 and destination.read_bytes() == retained
    republished = publish(app, review, export_format, policy_owner_id=USER, policy_revision=1)
    assert republished.status_code == 200, republished.text
    deleted = delete(app, export_id)
    assert deleted.status_code == 204, deleted.text
    assert not destination.exists()


@FORMATS
def test_permission_denied_item_folder_defers_then_converges(app, export_format):
    review = seeded_review(app, USER, f"denied-{export_format}")
    item = relocate(app, review, Path("share") / "Movie (2020)").parent
    published = publish(app, review, export_format).json()
    _status, original, destination = observe(app, published["id"])
    bump_policy(app.accounts)
    item.chmod(0)
    try:
        assert settle_due(app, 0) == "failed"
        assert job_state(app, published["id"])[0] == "withdrawing"
        status, retained, _destination = observe(app, published["id"])
        assert status["serving"] and retained == original
    finally:
        item.chmod(0o755)
    assert destination.read_bytes() == original
    assert settle_due(app, 1) == "succeeded"
    assert destination.read_bytes() == observe(app, published["id"])[1]
    republished = publish(app, review, export_format, policy_owner_id=USER, policy_revision=1)
    assert republished.status_code == 200, republished.text
    assert delete(app, published["id"]).status_code == 204
    assert not destination.exists()


@FORMATS
def test_non_enoent_error_at_the_retained_path_defers(app, export_format):
    review = seeded_review(app, USER, f"not-a-directory-{export_format}")
    item = relocate(app, review, Path("share") / "Movie (2020)").parent
    published = publish(app, review, export_format).json()
    _status, original, destination = observe(app, published["id"])
    bump_policy(app.accounts)
    away = item.with_name("Movie (2020) away")
    item.rename(away)
    item.write_bytes(b"not a directory")
    try:
        assert settle_due(app, 0) == "failed"
        assert job_state(app, published["id"])[0] == "withdrawing"
        assert observe(app, published["id"])[0]["serving"]
    finally:
        item.unlink()
        away.rename(item)
    assert destination.read_bytes() == original
    assert settle_due(app, 1) == "succeeded"


@pytest.mark.skipif(HDIUTIL is None, reason="hdiutil is required for a real mount")
@FORMATS
def test_real_unmount_keeps_evidence_and_converges_on_return(app, tmp_path, export_format):
    share = app.media / "share"
    with disk_image(tmp_path, share) as (attach, detach):
        review = seeded_review(app, USER, f"mounted-{export_format}")
        relocate(app, review, Path("share") / "Movie (2020)")
        published = publish(app, review, export_format).json()
        _status, original, destination = observe(app, published["id"])
        bump_policy(app.accounts)
        detach()
        assert share.is_dir() and not any(share.iterdir())
        assert settle_due(app, 0) == "failed"
        assert_withdrawn_as_absent(app, published["id"])
        attach()
        assert_converges_after_return(
            app, review, export_format, published["id"], destination, original
        )


@FORMATS
def test_flat_library_on_a_removed_mount_directory_converges(app, export_format):
    review = seeded_review(app, USER, f"flat-{export_format}")
    relocate(app, review, Path("share"))
    published = publish(app, review, export_format).json()
    _status, original, destination = observe(app, published["id"])
    bump_policy(app.accounts)
    share = app.media / "share"
    away = share.with_name("share-unmounted")
    share.rename(away)
    try:
        assert settle_due(app, 0) == "failed"
        assert_withdrawn_as_absent(app, published["id"])
        sweep(app)
        assert job_state(app, published["id"])[0] == "withdrawn"
    finally:
        away.rename(share)
    assert_converges_after_return(
        app, review, export_format, published["id"], destination, original
    )


@FORMATS
def test_returned_owned_bytes_without_media_are_removed(app, export_format):
    review = seeded_review(app, USER, f"returned-{export_format}")
    media = relocate(app, review, Path("share"))
    published = publish(app, review, export_format).json()
    _status, original, destination = observe(app, published["id"])
    bump_policy(app.accounts)
    share = app.media / "share"
    away = share.with_name("share-unmounted")
    share.rename(away)
    assert settle_due(app, 0) == "failed"
    away.rename(share)
    media.unlink()
    assert destination.read_bytes() == original
    sweep(app)
    assert settle_due(app, 1) == "failed"
    status, retained, _destination = observe(app, published["id"])
    assert job_state(app, published["id"])[0] == "withdrawn"
    assert not destination.exists() and not status["serving"] and retained is None
    assert settle_due(app, 2) is None
    destination.write_bytes(original)
    sweep(app)
    assert settle_due(app, 3) == "failed"
    assert not destination.exists()


@FORMATS
def test_foreign_bytes_at_a_returned_path_stay_untouched(app, export_format):
    review = seeded_review(app, USER, f"foreign-{export_format}")
    relocate(app, review, Path("share"))
    published = publish(app, review, export_format).json()
    destination = observe(app, published["id"])[2]
    bump_policy(app.accounts)
    share = app.media / "share"
    away = share.with_name("share-unmounted")
    share.rename(away)
    assert settle_due(app, 0) == "failed"
    away.rename(share)
    destination.write_bytes(b"foreign")
    sweep(app)
    assert job_state(app, published["id"])[0] == "withdrawn"
    with app.accounts.factory() as db:
        status = observe(app, published["id"])[0]
        assert retry_regeneration(db, published["id"], status["publication_id"])
    assert settle_due(app, 1) == "failed"
    assert job_state(app, published["id"])[0] == "withdrawn"
    republished = publish(app, review, export_format, policy_owner_id=USER, policy_revision=1)
    assert republished.status_code == 409 and "export_file_collision" in republished.text
    deleted = delete(app, published["id"])
    assert deleted.status_code == 409 and "export_file_collision" in deleted.text
    assert destination.read_bytes() == b"foreign"


@FORMATS
def test_delete_removes_an_owned_file_without_media(app, export_format):
    review = seeded_review(app, USER, f"delete-without-media-{export_format}")
    media = relocate(app, review, Path("share"))
    published = publish(app, review, export_format).json()
    destination = observe(app, published["id"])[2]
    media.unlink()
    deleted = delete(app, published["id"])
    assert deleted.status_code == 204, deleted.text
    assert not destination.exists()
    with app.accounts.factory() as db:
        assert db.get(MediaItemExportRow, published["id"]) is None


@FORMATS
def test_render_failure_with_missing_export_directory_settles(app, monkeypatch, export_format):
    review = seeded_review(app, USER, f"render-failure-{export_format}")
    published = publish(app, review, export_format).json()
    destination = observe(app, published["id"])[2]
    shutil.rmtree(destination.parent)

    def refuse(*_args, **_kwargs):
        raise ValueError("synthetic confirmed render failure")

    monkeypatch.setattr("scanner.runtime.export_regeneration.prepare_retained_export", refuse)
    bump_policy(app.accounts)
    assert [settle_due(app, hours) for hours in range(4)] == ["failed", None, None, None]
    assert_withdrawn_as_absent(app, published["id"])
    for _attempt in range(2):
        deleted = delete(app, published["id"])
        assert deleted.status_code == 204, deleted.text
    status, retained, _destination = observe(app, published["id"])
    assert job_state(app, published["id"])[0] == "withdrawn" and retained is None
    assert not status["serving"] and not status["retryable"] and not status["failed"]


@FORMATS
def test_audit_rejection_propagates_without_withdrawal(app, monkeypatch, export_format):
    published = publish(app, seeded_review(app, USER, f"audit-{export_format}"), export_format)
    _status, original, destination = observe(app, published.json()["id"])

    def reject(*_args, **_kwargs):
        raise AuditEventRejected("synthetic audit rejection")

    monkeypatch.setattr("scanner.runtime.export_regeneration.stage_event", reject)
    bump_policy(app.accounts)
    with pytest.raises(AuditEventRejected):
        settle_due(app, 0)
    assert job_state(app, published.json()["id"])[0] == "running"
    assert destination.read_bytes() == original
