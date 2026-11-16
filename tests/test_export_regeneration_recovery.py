import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scanner.db import AuditEventRow, MediaItemExportRow
from scanner.persistence.repository.export_regeneration import (
    claim_regeneration,
    queue_regenerations,
    retry_regeneration,
    sweep_regenerations,
)
from scanner.runtime.export_regeneration import settle_regeneration
from tests.test_export_regeneration import publish_policy_review
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app
from tests.test_review_policy_view import enforce
from tests.test_review_policy_view import policy_review as policy_review


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("withdrawing", [False, True])
def test_hard_exit_reconciles_job_file_database_and_audit(
    policy_review, format, committed, withdrawing
):
    accounts, review_id = policy_review
    from scanner.db import SavedReviewRow

    with accounts.factory() as db:
        db.get(SavedReviewRow, review_id).mcf_document.material_end_seconds = 100
        db.commit()
    first = publish_policy_review(accounts, review_id, format)
    with accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        path, old = Path(row.destination_path), row.content_bytes
    enforce(accounts, severities=["swearing"])
    script = """
import os, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from scanner.persistence.repository.export_regeneration import claim_regeneration
from scanner.runtime import export_regeneration as worker
engine = create_engine(sys.argv[1])
with Session(engine) as db:
    job = claim_regeneration(db)
    commit = db.commit
    def crash():
        if sys.argv[2] == "True":
            commit()
        os._exit(73)
    db.commit = crash
    if sys.argv[3] == "True":
        def fail(*args, **kwargs):
            raise ValueError("injected preparation failure")
        worker.prepare_retained_export = fail
    worker.settle_regeneration(db, job)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            script,
            str(accounts.engine.url),
            str(committed),
            str(withdrawing),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 73, result.stderr.decode()
    assert list(path.parent.glob(".*.editarr-recovery.json"))
    with accounts.factory() as db:
        row = db.get(MediaItemExportRow, first["id"])
        expected = row.content_bytes
        state = row.regeneration.state
        sweep_regenerations(db)
        job = claim_regeneration(db, now=time.time() + 10)
        assert (path.read_bytes() if path.exists() else None) == expected
        assert not list(path.parent.glob(".*.editarr-recovery.json"))
        assert not list(path.parent.glob(".*.bak"))
        if committed:
            assert job is None
            assert state == ("withdrawn" if withdrawing else "succeeded")
            event = db.query(AuditEventRow).filter_by(action="export.regenerate").one()
            assert event.outcome == ("failed" if withdrawing else "succeeded")
            if withdrawing:
                snapshot = json.loads(row.snapshot_json)
                assert retry_regeneration(db, row.id, snapshot["publication_id"])
                job = claim_regeneration(db)
        else:
            assert expected == old
            assert job is not None
            assert db.query(AuditEventRow).filter_by(action="export.regenerate").count() == 0
        if job is not None:
            assert settle_regeneration(db, job) == "succeeded"
        row = db.get(MediaItemExportRow, first["id"])
        assert row.content_bytes == path.read_bytes()
        assert row.policy_revision > 0
        assert json.loads(row.snapshot_json)["source_review_id"] == review_id
        assert db.query(MediaItemExportRow).count() == 1


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_regeneration_preserves_published_source_membership_links(policy_review, format):
    from scanner.db import MediaItemExportScanRow, SavedReviewRow

    accounts, review_id = policy_review
    with accounts.factory() as db:
        db.get(SavedReviewRow, review_id).mcf_document.material_end_seconds = 100
        db.commit()
    first = publish_policy_review(accounts, review_id, format)
    with accounts.factory() as db:
        link = db.query(MediaItemExportScanRow).filter_by(media_item_export_id=first["id"]).one()
        original_role = link.role
        original_sources = json.loads(db.get(MediaItemExportRow, first["id"]).snapshot_json)[
            "sources"
        ]
        review = db.get(SavedReviewRow, review_id)
        review.memberships[0].role = "both"
        db.commit()
    enforce(accounts, severities=["swearing"])
    with accounts.factory() as db:
        job = claim_regeneration(db)
        assert settle_regeneration(db, job) == "succeeded"
        link = db.query(MediaItemExportScanRow).filter_by(media_item_export_id=first["id"]).one()
        row = db.get(MediaItemExportRow, first["id"])
        snapshot = json.loads(row.snapshot_json)
        assert link.role == original_role
        assert snapshot["sources"] == original_sources


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_failed_transfer_keeps_current_policy_owner_after_review_deletion(app, monkeypatch, format):
    from scanner.db import SavedReviewRow
    from scanner.runtime import export_regeneration as worker
    from tests.multi_user_access_support import ADMIN, USER

    review = seeded_review(app, USER, "failed-transfer")
    first = publish(app, review, format).json()
    with app.accounts.factory() as db:
        db.get(SavedReviewRow, review.id).owner_id = ADMIN
        queue_regenerations(db, review_ids=[review.id])
        db.commit()
        job = claim_regeneration(db)
        with monkeypatch.context() as patched:

            def fail(*args, **kwargs):
                raise ValueError("injected preparation failure")

            patched.setattr(worker, "prepare_retained_export", fail)
            assert settle_regeneration(db, job) == "failed"
        row = db.get(MediaItemExportRow, first["id"])
        assert row.policy_owner_id == ADMIN
        row.review_id = row.exported_format_revision = None
        db.flush()
        db.delete(db.get(SavedReviewRow, review.id))
        db.commit()
        snapshot = json.loads(row.snapshot_json)
        assert retry_regeneration(db, row.id, snapshot["publication_id"])
        resumed = claim_regeneration(db)
        assert resumed.policy_owner_id == ADMIN
        assert settle_regeneration(db, resumed) == "succeeded"
        assert row.original_author_id == USER
