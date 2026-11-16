"""An explicit DELETE on an absent retained path keeps the ownership evidence."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from scanner.db import AuditEventRow, MediaItemExportRow, SavedReviewRow
from scanner.persistence.repository.export_regeneration import (
    claim_regeneration,
    retry_regeneration,
)
from scanner.runtime.export_regeneration import settle_regeneration
from tests.multi_user_access_support import PEER_USER, USER
from tests.test_export_discovery_http import discovered
from tests.test_export_regeneration_boundaries import later, observe
from tests.test_export_regeneration_convergence import job_state, settle_due
from tests.test_export_regeneration_failures import bump_policy
from tests.test_export_regeneration_ownership import HDIUTIL, disk_image, relocate, sweep
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app

FORMATS = pytest.mark.parametrize("export_format", ["edl", "mcf"])
MOUNTED = pytest.mark.skipif(HDIUTIL is None, reason="hdiutil is required for a real mount")


def deletion_request(app, export_id):
    """Return one DELETE request, so repeating it sends identical revision and UUID."""
    status = observe(app, export_id)[0]
    path = (
        f"/api/exports/{export_id}?revision={status['publication_revision']}"
        f"&publication_id={status['publication_id']}"
    )
    return lambda: app.send("DELETE", path, app.accounts.unsafe_cookie(USER))


def deletions(app, export_id):
    with app.accounts.factory() as db:
        return (
            db.query(AuditEventRow)
            .filter_by(action="export.delete", resource=f"export:{export_id}")
            .count()
        )


def keyed_rows(app, export_id):
    """Return the ids of every row sharing `export_id`'s one-current-export key."""
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        return [
            other.id
            for other in db.query(MediaItemExportRow).filter_by(
                media_item_id=row.media_item_id,
                media_source_id=row.media_source_id,
                format=row.format,
                scope=row.scope,
                target_user_id=row.target_user_id,
            )
        ]


def assert_deleted_record(app, export_id):
    """The row stays as a non-serving record with nothing pending, failed, or retryable."""
    status, retained, _destination = observe(app, export_id)
    assert job_state(app, export_id)[0] == "withdrawn"
    assert retained is None and not status["serving"] and not status["current"]
    assert status["regeneration_state"] == "idle" and not status["regenerating"]
    assert not status["failed"] and not status["retryable"]
    assert status["failure_reason"] is None


def delete_twice(app, export_id):
    request = deletion_request(app, export_id)
    first, second = request(), request()
    assert (first.status_code, second.status_code) == (204, 204), (first.text, second.text)
    assert_deleted_record(app, export_id)
    assert deletions(app, export_id) == 1


@contextmanager
def deleted_while_unmounted(app, tmp_path, export_format):
    """Publish on a real disk image, DELETE twice while detached, then re-attach."""
    share = app.media / "share"
    with disk_image(tmp_path, share) as (attach, detach):
        review = seeded_review(app, USER, f"deleted-{export_format}")
        relocate(app, review, Path("share") / "Movie (2020)")
        published = publish(app, review, export_format).json()
        _status, original, destination = observe(app, published["id"])
        detach()
        assert share.is_dir() and not any(share.iterdir())
        delete_twice(app, published["id"])
        attach()
        assert destination.read_bytes() == original
        yield review, published["id"], destination, original


@MOUNTED
@FORMATS
def test_republish_replaces_the_returned_file_of_a_deleted_publication(
    app, tmp_path, export_format
):
    with deleted_while_unmounted(app, tmp_path, export_format) as (review, export_id, path, _):
        republished = publish(app, review, export_format)
        assert republished.status_code == 200, republished.text
        assert republished.json()["id"] == export_id
        assert keyed_rows(app, export_id) == [export_id]
        status, retained, _destination = observe(app, export_id)
        assert status["serving"] and status["current"] and path.read_bytes() == retained


@MOUNTED
@FORMATS
def test_delete_removes_the_returned_file_of_a_deleted_publication(app, tmp_path, export_format):
    with deleted_while_unmounted(app, tmp_path, export_format) as (_review, export_id, path, _):
        deleted = deletion_request(app, export_id)()
        assert deleted.status_code == 204, deleted.text
        assert not path.exists()
        with app.accounts.factory() as db:
            assert db.get(MediaItemExportRow, export_id) is None


@MOUNTED
@FORMATS
def test_sweep_removes_the_returned_file_and_publish_reuses_the_record(
    app, tmp_path, export_format
):
    with deleted_while_unmounted(app, tmp_path, export_format) as (review, export_id, path, _):
        assert settle_due(app, 1) is None
        sweep(app)
        assert not path.exists()
        assert settle_due(app, 2) is None
        assert_deleted_record(app, export_id)
        republished = publish(app, review, export_format)
        assert republished.status_code == 200, republished.text
        assert republished.json()["id"] == export_id
        assert keyed_rows(app, export_id) == [export_id]
        assert path.read_bytes() == observe(app, export_id)[1]


@MOUNTED
@FORMATS
def test_foreign_bytes_after_a_deleted_publication_stay_untouched(app, tmp_path, export_format):
    with deleted_while_unmounted(app, tmp_path, export_format) as (review, export_id, path, _):
        path.write_bytes(b"foreign")
        sweep(app)
        assert path.read_bytes() == b"foreign"
        republished = publish(app, review, export_format)
        assert republished.status_code == 409 and "export_file_collision" in republished.text
        deleted = deletion_request(app, export_id)()
        assert deleted.status_code == 409 and "export_file_collision" in deleted.text
        assert path.read_bytes() == b"foreign"
        assert_deleted_record(app, export_id)


@contextmanager
def unmounted(app):
    share = app.media / "share"
    away = share.with_name("share-unmounted")
    share.rename(away)
    try:
        yield
    finally:
        away.rename(share)


@FORMATS
def test_deleted_publication_is_never_regenerated_or_retried(app, export_format):
    review = seeded_review(app, USER, f"never-regenerated-{export_format}")
    relocate(app, review, Path("share"))
    published = publish(app, review, export_format).json()
    _status, original, destination = observe(app, published["id"])
    with unmounted(app):
        delete_twice(app, published["id"])
        bump_policy(app.accounts)
        assert settle_due(app, 0) is None
    with app.accounts.factory() as db:
        status = observe(app, published["id"])[0]
        assert not retry_regeneration(db, published["id"], status["publication_id"])
    assert settle_due(app, 1) is None
    assert destination.read_bytes() == original
    sweep(app)
    assert not destination.exists()
    assert settle_due(app, 2) is None
    assert_deleted_record(app, published["id"])


@FORMATS
def test_delete_supersedes_a_running_claim(app, export_format):
    review = seeded_review(app, USER, f"supersedes-{export_format}")
    relocate(app, review, Path("share"))
    published = publish(app, review, export_format).json()
    destination = observe(app, published["id"])[2]
    bump_policy(app.accounts)
    with app.accounts.factory() as db:
        claim = claim_regeneration(db, now=later(0))
    with unmounted(app):
        delete_twice(app, published["id"])
        with app.accounts.factory() as db:
            assert settle_regeneration(db, claim) == "stale"
    assert_deleted_record(app, published["id"])
    sweep(app)
    assert not destination.exists()


@FORMATS
def test_publish_replaces_a_deleted_record_of_another_version_at_its_path(app, export_format):
    review = seeded_review(app, USER, f"other-version-{export_format}")
    relocate(app, review, Path("share"))
    published = publish(app, review, export_format).json()
    _status, original, destination = observe(app, published["id"])
    with unmounted(app):
        delete_twice(app, published["id"])
    with app.accounts.factory() as db:
        db.get(MediaItemExportRow, published["id"]).media_source_id = "retired-version"
        db.commit()
    assert destination.read_bytes() == original
    republished = publish(app, review, export_format)
    assert republished.status_code == 200, republished.text
    with app.accounts.factory() as db:
        source = db.get(SavedReviewRow, review.id).media_source_id
        rows = db.query(MediaItemExportRow).filter_by(destination_path=str(destination)).all()
        assert [(row.id, row.media_source_id) for row in rows] == [
            (republished.json()["id"], source)
        ]
        assert destination.read_bytes() == rows[0].content_bytes


@FORMATS
def test_deleted_record_is_discoverable_to_its_owner_and_hidden_like_an_absent_id(
    app, export_format
):
    review = seeded_review(app, USER, f"discovery-{export_format}")
    relocate(app, review, Path("share"))
    published = publish(app, review, export_format).json()
    with unmounted(app):
        delete_twice(app, published["id"])
    [record] = discovered(app, review)
    assert record["id"] == published["id"]
    assert not record["serving"] and record["regeneration_state"] == "idle"
    assert not record["retryable"] and record["failure_reason"] is None
    hidden = app.send("GET", f"/api/exports/{published['id']}", app.accounts.cookie(PEER_USER))
    absent = app.send(
        "GET", f"/api/exports/{published['id'] + 1000}", app.accounts.cookie(PEER_USER)
    )
    assert hidden.status_code == absent.status_code == 404
    assert hidden.content == absent.content
