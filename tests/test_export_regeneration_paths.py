"""Regeneration recreates missing export directories and withdraws only from proven absence."""

import json
import shutil
from pathlib import Path

import pytest

from scanner.db import MediaItemExportRow, ScanRow
from scanner.persistence.repository.export_regeneration import retry_regeneration
from tests.multi_user_access_support import USER
from tests.test_export_regeneration_boundaries import observe
from tests.test_export_regeneration_convergence import job_state, nested_review, settle_due
from tests.test_export_regeneration_failures import bump_policy
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app

FORMATS = pytest.mark.parametrize("export_format", ["edl", "mcf"])


def media_path(app, export_id):
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, export_id)
        return Path(json.loads(row.snapshot_json)["media_path"])


@FORMATS
def test_missing_export_directory_with_live_media_regenerates(app, export_format):
    review = seeded_review(app, USER, "missing-export-directory")
    published = publish(app, review, export_format).json()
    _status, original, destination = observe(app, published["id"])
    media = media_path(app, published["id"])
    media_before = media.read_bytes()
    shutil.rmtree(destination.parent)
    bump_policy(app.accounts)
    assert settle_due(app, 0) == "succeeded"
    status, retained, destination = observe(app, published["id"])
    assert status["regeneration_state"] == "succeeded" and status["serving"]
    assert status["policy_revision"] == 1 and retained == original
    assert destination.read_bytes() == retained
    assert media.read_bytes() == media_before
    assert settle_due(app, 1) is None
    republished = publish(app, review, export_format, policy_owner_id=USER, policy_revision=1)
    assert republished.status_code == 200, republished.text


@FORMATS
def test_recreated_export_directory_keeps_foreign_bytes(app, export_format):
    published = publish(app, seeded_review(app, USER, "foreign-directory"), export_format).json()
    _status, _original, destination = observe(app, published["id"])
    shutil.rmtree(destination.parent)
    destination.parent.mkdir()
    destination.write_bytes(b"foreign")
    bump_policy(app.accounts)
    assert settle_due(app, 0) == "failed"
    status, retained, _destination = observe(app, published["id"])
    assert destination.read_bytes() == b"foreign"
    assert retained is None and not status["serving"]


@FORMATS
def test_removed_item_folder_under_present_library_withdraws(app, export_format):
    review = nested_review(app, USER, "removed-item")
    published = publish(app, review, export_format).json()
    _status, _original, destination = observe(app, published["id"])
    sibling = app.media / "share" / "sibling.mkv"
    sibling.write_bytes(b"foreign")
    shutil.rmtree(media_path(app, published["id"]).parent)
    bump_policy(app.accounts)
    assert settle_due(app, 0) == "failed"
    status, retained, _destination = observe(app, published["id"])
    assert job_state(app, published["id"])[0] == "withdrawn"
    assert status["failed"] and status["retryable"] and not status["serving"]
    assert retained is None and not destination.exists()
    assert sibling.read_bytes() == b"foreign"
    assert settle_due(app, 1) is None


@FORMATS
def test_removed_media_with_retained_directory_converges(app, export_format):
    published = publish(app, seeded_review(app, USER, "removed-media"), export_format).json()
    media_path(app, published["id"]).unlink()
    bump_policy(app.accounts)
    assert settle_due(app, 0) == "failed"
    status, retained, destination = observe(app, published["id"])
    assert retained is None and not destination.exists() and not status["serving"]
    assert settle_due(app, 3) is None


@FORMATS
def test_preexisting_symlinked_library_remains_supported(app, tmp_path, export_format):
    review = seeded_review(app, USER, "linked-library")
    alias = tmp_path / "library-alias"
    alias.symlink_to(app.media, target_is_directory=True)
    with app.accounts.factory() as db:
        scan = db.query(ScanRow).one()
        scan.source_path = str(alias / Path(scan.source_path).name)
        scan.resolved_local_path = str(alias / Path(scan.resolved_local_path).name)
        db.commit()
    response = publish(app, review, export_format)
    assert response.status_code == 200, response.text
    published = response.json()
    _status, _original, destination = observe(app, published["id"])
    assert destination.parent.resolve() == destination.parent
    bump_policy(app.accounts)
    assert settle_due(app, 0) == "succeeded"
    assert destination.read_bytes() == observe(app, published["id"])[1]
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, published["id"])
        Path(json.loads(row.snapshot_json)["media_path"]).unlink()
        row.regeneration.state = "failed"
        row.regeneration.failure_reason = "retries_exhausted"
        db.commit()
        status = observe(app, published["id"])[0]
        assert retry_regeneration(db, published["id"], status["publication_id"])
    assert settle_due(app, 1) == "failed"
    assert not destination.exists()
