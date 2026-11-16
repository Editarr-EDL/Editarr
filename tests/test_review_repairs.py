"""Repair commands expose preserved cues without silently resuming broken reviews."""

import json
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from scanner.db import initialize_sqlite_connection
from scanner.persistence.migrations import adoption
from scanner.repository import get_saved_review, list_saved_reviews
from tests.activity_migration_support import _upgrade
from tests.test_review_policy_migration import _built


@pytest.fixture
def broken_review(tmp_path, monkeypatch):
    from scanner import repository

    monkeypatch.setattr(repository, "get_video_duration", lambda _path: 120.0)
    path = _built(tmp_path)
    _upgrade(path, "0019_review_policy_evidence")
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE review_filters SET id = '57c19c86-9cb8-472a-b6bc-d89496609d13' "
            "WHERE id = 'review-filter-001'"
        )
        database.execute(
            "INSERT OR REPLACE INTO review_mcf_documents "
            "(review_id, material_start_seconds, material_end_seconds) "
            "SELECT review_id, start_seconds, end_seconds FROM review_synchronizations"
        )
        database.execute("DELETE FROM scans WHERE id = 'legacy-scan-001'")
    adoption.upgrade_existing_database(path, path.parent)
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", initialize_sqlite_connection)
    with Session(engine) as database:
        yield database, "review-legacy-001"
    engine.dispose()


def test_list_flags_broken_review_without_loading_missing_scans(broken_review):
    database, review_id = broken_review
    reviews = list_saved_reviews(database, media_item_id="media-legacy")
    summary = next(value for value in reviews if value.id == review_id)
    assert summary.repair.missing_scan_ids == ["legacy-scan-001"]
    assert summary.repair.recover.kept_cues == 1
    assert summary.repair.recover.removed_memberships == 1
    assert summary.repair.community.available is True
    assert next(value for value in reviews if value.id != review_id).repair is None


def test_detail_requires_explicit_repair(broken_review):
    from scanner.persistence.repository.review_repairs import ReviewNeedsRepair

    database, review_id = broken_review
    with pytest.raises(ReviewNeedsRepair):
        get_saved_review(database, review_id)


def test_recovery_counts_and_drops_lost_credits_and_evidence(broken_review):
    from scanner.db import ReviewPolicyEvidenceRow, ReviewScanRepairRow
    from scanner.persistence.repository.review_repairs import recover_review, repair_summary

    database, review_id = broken_review
    record = database.query(ReviewScanRepairRow).filter_by(review_id=review_id).one()
    preserved = json.loads(record.preserved_json)
    preserved["review_accepted_credits"] = [{"scan_id": record.missing_scan_id}]
    record.preserved_json = json.dumps(preserved)
    database.commit()
    summary = repair_summary(database, review_id)
    assert summary.recover.model_dump() == {
        "kept_cues": 1,
        "removed_memberships": 1,
        "removed_accepted_credits": 1,
    }
    lost_findings = {value["source_finding_id"] for value in preserved["review_policy_evidence"]}
    recovered = recover_review(database, review_id)
    assert recovered.accepted_credits is None
    assert all(value.scan_id != record.missing_scan_id for value in recovered.memberships)
    assert not (
        lost_findings
        & {
            value.source_finding_id
            for value in database.query(ReviewPolicyEvidenceRow).filter_by(review_id=review_id)
        }
    )
    assert repair_summary(database, review_id) is None


def test_recover_keeps_manual_cues_and_then_saves_and_exports(broken_review):
    from scanner.models import ReviewSave
    from scanner.persistence.repository.review_repairs import recover_review
    from scanner.repository import export_saved_review_edl, save_saved_review

    database, review_id = broken_review
    recovered = recover_review(database, review_id)
    assert recovered.repair is None
    assert len(recovered.filters) == 1
    cue = recovered.filters[0]
    assert cue.source_finding_id is None
    assert (cue.start_seconds, cue.end_seconds, cue.label) == (15.0, 17.0, "Synthetic finding")
    assert cue.source_scan_id != "legacy-scan-001"
    request = ReviewSave.model_validate(recovered.model_dump(include=set(ReviewSave.model_fields)))
    saved = save_saved_review(database, review_id, request)
    assert saved.filters[0].source_finding_id is None
    exported = export_saved_review_edl(
        database,
        review_id,
        saved.revision,
        policy_owner_id=saved.policy_owner_id,
        policy_revision=saved.policy_revision,
    )
    assert exported is not None
    assert recover_review(database, review_id).revision == saved.revision


@pytest.mark.parametrize("mapped", [False, True])
def test_recovered_review_can_delete_its_existing_export_and_replace_it(
    broken_review, monkeypatch, mapped
):
    from pathlib import Path

    from scanner.db import MediaItemExportRow
    from scanner.models import ExportFormat
    from scanner.persistence.repository.review_repairs import recover_review
    from scanner.repository import delete_library_item_export, export_saved_review_edl

    database, review_id = broken_review
    row = database.query(MediaItemExportRow).filter_by(review_id=review_id).one()
    media = row.media_item
    destination = Path(media.path).with_suffix(".edl")
    destination.write_text("historical export")
    assert row.scans == []
    recovered = recover_review(database, review_id)
    assert destination.read_text() == "historical export"
    if mapped:
        from types import SimpleNamespace

        from scanner.persistence.repository import repository_exports

        media.path = "/mapped-source/movie.mkv"
        database.commit()
        monkeypatch.setattr(
            repository_exports,
            "resolve_local_path",
            lambda _db, _path: SimpleNamespace(local_path=str(destination.with_suffix(".mkv"))),
            raising=False,
        )
    assert delete_library_item_export(
        database,
        server_id=media.server_id,
        jellyfin_item_id=media.jellyfin_id,
        media_source_id=row.media_source_id,
        export_format=ExportFormat.EDL,
    )
    assert not destination.exists()
    assert database.get(MediaItemExportRow, row.id) is None
    media.path = str(destination.with_suffix(".mkv"))
    database.commit()
    assert (
        export_saved_review_edl(
            database,
            review_id,
            recovered.revision,
            policy_owner_id=recovered.policy_owner_id,
            policy_revision=recovered.policy_revision,
        )
        is not None
    )
    assert destination.is_file()


def test_multiple_recovered_manual_cues_can_be_saved_and_retimed(broken_review):
    from scanner.db import ReviewScanRepairRow
    from scanner.models import ReviewRetimeRequest, ReviewSave
    from scanner.persistence.repository.review_repairs import recover_review
    from scanner.repository import retime_saved_review_draft, save_saved_review

    database, review_id = broken_review
    record = database.query(ReviewScanRepairRow).filter_by(review_id=review_id).one()
    preserved = json.loads(record.preserved_json)
    cue = dict(
        preserved["review_filters"][0],
        id=str(uuid4()),
        position=1,
        start_seconds=25,
        end_seconds=27,
    )
    preserved["review_filters"].append(cue)
    record.preserved_json = json.dumps(preserved)
    database.commit()
    recovered = recover_review(database, review_id)
    assert len(recovered.filters) == 2
    assert all(value.source_finding_id is None for value in recovered.filters)
    data = recovered.model_dump(include=set(ReviewSave.model_fields))
    saved = save_saved_review(database, review_id, ReviewSave.model_validate(data))
    data = saved.model_dump(include=set(ReviewRetimeRequest.model_fields))
    data.update(
        current_synchronization=saved.synchronization,
        proposed_synchronization=saved.synchronization,
        new_manual_filters=[],
    )
    retimed = retime_saved_review_draft(
        database, review_id, ReviewRetimeRequest.model_validate(data)
    )
    assert len(retimed.filters) == 2


def test_client_cannot_forge_an_additional_null_provenance_cue(broken_review):
    from scanner.models import ReviewSave
    from scanner.persistence.repository.review_repairs import recover_review
    from scanner.repository import ReviewMembershipError, save_saved_review

    database, review_id = broken_review
    recovered = recover_review(database, review_id)
    data = recovered.model_dump(include=set(ReviewSave.model_fields))
    data["filters"].append(dict(data["filters"][0], id=str(uuid4())))
    with pytest.raises(ReviewMembershipError, match="provenance is required"):
        save_saved_review(database, review_id, ReviewSave.model_validate(data))


def test_recovery_failure_rolls_back_manual_scan_and_keeps_flags(broken_review, monkeypatch):
    from scanner.db import ReviewScanRepairRow, ScanRow
    from scanner.persistence.repository import review_policy_evidence
    from scanner.persistence.repository.review_repairs import recover_review

    database, review_id = broken_review
    before = database.query(ScanRow).count()

    def fail(*_args):
        raise RuntimeError("injected recovery failure")

    monkeypatch.setattr(review_policy_evidence, "bind_review_evidence", fail)
    with pytest.raises(RuntimeError, match="injected recovery failure"):
        recover_review(database, review_id)
    assert database.query(ScanRow).count() == before
    assert database.query(ReviewScanRepairRow).filter_by(review_id=review_id).count() == 1
