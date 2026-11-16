"""Export bytes and records use the same effective review as detail."""

from pathlib import Path

import pytest
from sqlalchemy import update

from scanner import repository
from scanner.db import (
    MediaItemExportRow,
    ReviewPolicyEvidenceRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
    UserFilterPreferenceRow,
)
from scanner.models import ExportFormat
from scanner.persistence.repository.review_policy import ReviewPolicyConflict
from tests.multi_user_access_support import PEER_USER, USER
from tests.test_review_policy_view import base_rows, enforce
from tests.test_review_policy_view import policy_review as policy_review


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_formats_use_owner_policy_and_record_revision(policy_review, format):
    accounts, review_id = policy_review
    before = base_rows(accounts, review_id)
    enforce(accounts, severities=["swearing"])
    with accounts.factory() as db:
        review = db.get(SavedReviewRow, review_id)
        review.mcf_document.material_end_seconds = 100
        db.commit()
        detail = repository.get_saved_review(db, review_id)
        export = getattr(repository, f"export_saved_review_{format}")(
            db,
            review_id,
            detail.revision,
            policy_revision=detail.policy_revision,
            policy_owner_id=detail.policy_owner_id,
        )
        path = Path(db.get(ScanRow, "scan").resolved_local_path).with_suffix(f".{format}")
        content = path.read_text()
        if format == "edl":
            assert "Evidence" in content
        else:
            assert "swearing=high=audio" in content
        row = db.query(MediaItemExportRow).filter_by(format=ExportFormat(format)).one()
        assert row.policy_owner_id == detail.policy_owner_id == export.policy_owner_id
        assert row.policy_revision == detail.policy_revision == export.policy_revision
    assert base_rows(accounts, review_id) == before


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_stale_policy_export_creates_no_sidecar_or_record(policy_review, format):
    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        with pytest.raises(ReviewPolicyConflict):
            getattr(repository, f"export_saved_review_{format}")(
                db, review_id, 0, policy_revision=0
            )
        assert db.query(MediaItemExportRow).count() == 0
        assert (
            not Path(db.get(ScanRow, "scan").resolved_local_path).with_suffix(f".{format}").exists()
        )


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("change", ["policy", "owner"])
def test_change_during_formatting_rejects_publication_and_preserves_sidecar(
    policy_review, monkeypatch, format, change
):
    from scanner.persistence.repository import review_export_content as preparation

    accounts, review_id = policy_review
    enforce(accounts, severities=["swearing"])
    with accounts.factory() as db:
        review = db.get(SavedReviewRow, review_id)
        review.mcf_document.material_end_seconds = 100
        db.commit()
        detail = repository.get_saved_review(db, review_id)
        path = Path(db.get(ScanRow, "scan").resolved_local_path).with_suffix(f".{format}")
        path.write_bytes(b"previous sidecar")
        original = getattr(preparation, f"export_{format}_content")

        def changing(*args, **kwargs):
            if change == "policy":
                db.execute(
                    update(UserFilterPreferenceRow)
                    .where(UserFilterPreferenceRow.user_id == USER)
                    .values(policy_revision=UserFilterPreferenceRow.policy_revision + 1)
                )
            else:
                db.execute(
                    update(SavedReviewRow)
                    .where(SavedReviewRow.id == review_id)
                    .values(owner_id=PEER_USER)
                )
            db.expire_all()
            return original(*args, **kwargs)

        monkeypatch.setattr(preparation, f"export_{format}_content", changing)
        with pytest.raises(ReviewPolicyConflict):
            getattr(repository, f"export_saved_review_{format}")(
                db,
                review_id,
                detail.revision,
                policy_owner_id=detail.policy_owner_id,
                policy_revision=detail.policy_revision,
            )
        assert path.read_bytes() == b"previous sidecar"
        assert db.query(MediaItemExportRow).count() == 0


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_output_precision_refusal_keeps_existing_sidecar_and_revision(policy_review, format):
    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        review = db.get(SavedReviewRow, review_id)
        review.filters[0].end_seconds = 2.0001
        finding = db.get(ScanFindingRow, review.filters[0].source_finding_id)
        finding.end_seconds = 2.0001
        binding = db.query(ReviewPolicyEvidenceRow).filter_by(review_id=review_id).one()
        binding.end_seconds = 2.0001
        scan = db.get(ScanRow, "scan")
        scan.duration_seconds = 2.0001
        review.mcf_document.material_end_seconds = 2.0
        db.commit()
        detail = repository.get_saved_review(db, review_id)
        path = Path(scan.resolved_local_path).with_suffix(f".{format}")
        path.write_bytes(b"previous sidecar")
        with pytest.raises(ReviewPolicyConflict) as refused:
            getattr(repository, f"export_saved_review_{format}")(
                db,
                review_id,
                detail.revision,
                policy_owner_id=detail.policy_owner_id,
                policy_revision=detail.policy_revision,
            )
        assert refused.value.code == "review_policy_timing_precision"
        assert path.read_bytes() == b"previous sidecar"
        assert db.query(MediaItemExportRow).count() == 0
        assert repository.get_saved_review(db, review_id) == detail
