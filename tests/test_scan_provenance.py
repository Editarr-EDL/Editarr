"""Historical scan evidence and explicit unknown provenance across local upgrades."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.models import ReviewFilterInput
from scanner.persistence.migrations import adoption
from scanner.persistence.repository.repository_rows import _scan_finding_from_row, _scan_from_row
from scanner.persistence.rows.db_reviews import ReviewFilterRow
from scanner.persistence.rows.db_scans import ScanFindingRow, ScanRow, ScanStageCoverageRow
from tests.community_schema_fixture import create_unversioned_fixture


def test_legacy_upgrade_keeps_scores_and_exposes_unknown_provenance(tmp_path):
    path = tmp_path / "legacy.db"
    create_unversioned_fixture(path).dispose()
    with sqlite3.connect(path) as database:
        before = database.execute(
            "SELECT id, scan_id, confidence FROM scan_findings"
        ).fetchall()
    generation = adoption.upgrade_existing_database(path, tmp_path)
    assert generation is not None
    assert adoption._inspect_database(path) == adoption.HEAD_REVISION

    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            scan = session.get(ScanRow, "legacy-scan-001")
            assert scan is not None
            response = _scan_from_row(scan).model_dump(mode="json")
            assert response["scanner_behavior_version"] is None
            assert response["release_version"] is None
            assert response["stage_coverage"] == []
            assert response["id"] == "legacy-scan-001"
            assert _scan_finding_from_row(session.get(ScanFindingRow, 101)).confidence == 0.9
            review_filter = session.get(ReviewFilterRow, ("review-legacy-001", "review-filter-001"))
            assert repository._review_filter(review_filter).confidence == 0.9
        with sqlite3.connect(path) as database:
            assert database.execute(
                "SELECT id, scan_id, confidence FROM scan_findings"
            ).fetchall() == before
            assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()


def test_unknown_manual_confidence_and_partial_coverage_serialize(tmp_path):
    path = tmp_path / "legacy.db"
    create_unversioned_fixture(path).dispose()
    adoption.upgrade_existing_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            scan = session.get(ScanRow, "legacy-scan-001")
            scan.stage_coverage.append(ScanStageCoverageRow(
                stage="audio.transcription", execution_id="attempt-1",
                status="partial", source="generated",
                intervals_json=json.dumps([{"start_seconds": 0, "end_seconds": 12}]),
                effective_settings_json=json.dumps({"language": "en"}),
                sampling_seconds=2.0,
            ))
            session.get(ScanFindingRow, 101).confidence = None
            review_filter = session.get(ReviewFilterRow, ("review-legacy-001", "review-filter-001"))
            review_filter.confidence = None
            session.commit()
            response = _scan_from_row(scan).model_dump(mode="json")
            assert response["stage_coverage"][0]["status"] == "partial"
            assert response["stage_coverage"][0]["effective_settings"] == {"language": "en"}
            scan.stage_coverage.append(ScanStageCoverageRow(
                stage="audio.transcription", execution_id="attempt-2",
                status="failed", source="generated",
            ))
            session.commit()
            assert [item.status for item in scan.stage_coverage] == ["partial", "failed"]
            assert response["stage_coverage"][0]["intervals"] == [
                {"start_seconds": 0.0, "end_seconds": 12.0}
            ]
            finding = _scan_finding_from_row(session.get(ScanFindingRow, 101))
            assert finding.model_dump()["confidence"] is None
            review = repository._review_filter(review_filter)
            assert ReviewFilterInput(
                **{**review.model_dump(), "id": "00000000-0000-4000-8000-000000000001"}
            ).confidence is None
            assert repository._review_filter(review_filter).model_dump(mode="json")[
                "confidence"
            ] is None
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(path) as database:
                database.execute(
                    "INSERT INTO scan_stage_coverage "
                    "(scan_id, stage, execution_id, status, source) VALUES (?, ?, ?, ?, ?)",
                    ("legacy-scan-001", "video", "attempt-1", "complete-ish", "generated"),
                )
    finally:
        engine.dispose()


def test_zero_finding_scan_can_record_disabled_and_failed_stages(tmp_path):
    path = tmp_path / "legacy.db"
    create_unversioned_fixture(path).dispose()
    adoption.upgrade_existing_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as session:
            scan = ScanRow(
                id="zero-findings", media_item_id="media-legacy", scan_number=8,
                origin="manual", source_path="/synthetic/movie.mkv",
                resolved_local_path="/synthetic/movie.mkv", status="complete",
                scan_audio=False, scan_video=False,
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
            scan.stage_coverage = [
                ScanStageCoverageRow(
                    stage="audio", execution_id="attempt-1", status="disabled", source="manual"
                ),
                ScanStageCoverageRow(
                    stage="visual", execution_id="attempt-1", status="failed", source="manual"
                ),
            ]
            session.add(scan)
            session.commit()
            response = _scan_from_row(scan).model_dump(mode="json")
            assert response["finding_count"] == 0
            assert [entry["status"] for entry in response["stage_coverage"]] == [
                "disabled", "failed",
            ]
    finally:
        engine.dispose()
