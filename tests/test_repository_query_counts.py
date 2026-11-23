"""Repository list and snapshot reads issue a bounded number of SQL statements."""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import event
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import (
    CommunityImportRow,
    CommunityImportTargetRow,
    CreditsDetectionRunRow,
    MediaItemExportRow,
    MediaItemExportScanRow,
    MediaItemRow,
    ReviewScanMembershipRow,
    ReviewScanRepairRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanMediaItemAssociationRow,
    ScanRow,
    ScanStageCoverageRow,
)
from scanner.models import McfCategoryFamily
from scanner.persistence.repository.repository_review_core import _snapshot_findings
from scanner.persistence.repository.repository_rows import _scan_from_row
from tests.review_repository_support import review_db

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _provenance(index: int) -> str:
    return json.dumps(
        {
            "service_url": "https://community.example",
            "contribution_id": f"c-{index}",
            "revision_id": f"r-{index}",
            "digest": f"sha256:{index}",
            "manifest_digest": "sha256:manifest",
            "signature_digest": "sha256:signature",
            "author_id": "author",
            "author_key_id": "key",
            "historical_key_status": "active",
            "artifact_schema": "mcf",
            "artifact_type": "scan",
            "timeline": "source_file",
            "source_duration_seconds": 100,
            "provenance": {"index": index},
            "coverage": [],
        }
    )


@contextmanager
def statement_count(db: Session):
    """Count SQL statements executed on the session's engine."""
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _finding(scan_id: str, start: float) -> ScanFindingRow:
    return ScanFindingRow(
        scan_id=scan_id,
        category=McfCategoryFamily.LANGUAGE,
        subcategory="swearing",
        start_seconds=start,
        end_seconds=start + 1,
        confidence=0.9,
        label="Evidence",
        source="audio",
        mcf_channel="audio",
        selected=True,
        edit_action="mute",
    )


def _add_scans(
    db: Session,
    count: int,
    *,
    start: int = 0,
    findings: int = 2,
    verified_finding_id: int | None = None,
) -> None:
    """Add complete scans with every relationship the scan model reads."""
    verification = {}
    if verified_finding_id is not None:
        verification = {
            "origin": "imported",
            "checkpoint_state": json.dumps(
                {
                    "identity_reuse": {
                        "source_scan_id": "scan",
                        "cues": [{"source_finding_id": verified_finding_id, "channel": "audio"}],
                    }
                }
            ),
        }
    for index in range(start, start + count):
        scan_id = f"scan-{index}"
        import_id = f"import-{index}"
        db.add(
            ScanRow(
                id=scan_id,
                media_item_id="media",
                scan_number=index + 2,
                source_path="/tmp/movie.mkv",
                resolved_local_path="/tmp/movie.mkv",
                status="complete",
                duration_seconds=100,
                scan_audio=True,
                scan_video=index % 2 == 0,
                scan_mode="credits_detection" if index % 2 else "content",
                updated_at=_BASE + timedelta(minutes=index),
                created_at=_BASE,
                **verification,
            )
        )
        db.flush()
        db.add_all(
            [
                ScanStageCoverageRow(
                    scan_id=scan_id,
                    stage="audio",
                    execution_id="run",
                    status="complete",
                    source="generated",
                    effective_settings_json='{"a": 1}',
                ),
                ScanMcfDocumentRow(
                    scan_id=scan_id,
                    source_version="1.1.0",
                    title=f"Title {index}",
                    material_start_seconds=0,
                    material_end_seconds=100,
                ),
                *(
                    [
                        CreditsDetectionRunRow(
                            scan_id=scan_id,
                            configuration_json=json.dumps(
                                {
                                    "detector_version": "v1",
                                    "tail_window_seconds": 60,
                                    "frame_sample_interval_seconds": 1,
                                }
                            ),
                            detector_version="v1",
                            result_json=json.dumps(
                                {
                                    "outcome": "no_candidate",
                                    "candidates": [],
                                    "best_candidate_id": None,
                                }
                            ),
                            completed_at=_BASE,
                        )
                    ]
                    if index % 2
                    else []
                ),
                CommunityImportRow(
                    id=import_id,
                    scan_id=scan_id,
                    service_url="https://community.example",
                    contribution_id=f"c-{index}",
                    revision_id=f"r-{index}",
                    digest=f"sha256:{index}",
                    author_id="author",
                    artifact_type="mcf",
                    artifact_bytes=b"artifact" if index % 3 else b"",
                    manifest_bytes=b"manifest",
                    signature_bytes=b"signature",
                    provenance_json=_provenance(index),
                ),
                CommunityImportTargetRow(
                    import_id=import_id,
                    media_item_id="media",
                    media_source_id="",
                    resolved_local_path="/tmp/movie.mkv",
                    target_evidence_json=json.dumps({"selected_channels": ["audio"]}),
                    review_required=False,
                ),
                *(_finding(scan_id, float(position)) for position in range(findings)),
            ]
        )
    db.commit()


def _lazy(db: Session, rows) -> list[str]:
    """Serialize scans with only default lazy relationship loading (the prior behavior)."""
    return [
        _scan_from_row(
            row,
            finding_count=db.query(ScanFindingRow).filter_by(scan_id=row.id).count(),
        ).model_dump_json()
        for row in rows
    ]


def _add_associated_scan(db: Session) -> None:
    """Reuse another item's scan for "media" through an explicit association."""
    db.add(
        MediaItemRow(
            id="other", server_id="server", jellyfin_id="other", item_type="Movie", name="Other"
        )
    )
    db.add(
        ScanRow(
            id="associated",
            media_item_id="other",
            scan_number=1,
            source_path="/tmp/other.mkv",
            resolved_local_path="/tmp/other.mkv",
            status="complete",
            scan_audio=True,
            scan_video=False,
            updated_at=_BASE - timedelta(days=1),
            created_at=_BASE - timedelta(days=1),
        )
    )
    db.flush()
    db.add(ScanMediaItemAssociationRow(scan_id="associated", media_item_id="media"))
    db.commit()


def _count(db: Session, read) -> int:
    db.expire_all()
    with statement_count(db) as statements:
        read()
    return len(statements)


def test_explicitly_linked_scan_includes_its_finding_count():
    with review_db() as db:
        _add_associated_scan(db)
        [linked] = repository.list_scans_for_library_item(
            db, jellyfin_item_id="item", associated_only=True,
        )
        assert linked.finding_count == 0

        db.add(_finding("associated", 1))
        db.commit()
        [linked] = repository.list_scans_for_library_item(
            db, jellyfin_item_id="item", associated_only=True,
        )
        assert linked.finding_count == 1


def test_scan_lists_do_not_scale_queries_with_scan_count():
    db = review_db()
    try:
        _add_associated_scan(db)

        def history():
            return repository.list_scan_history(db)

        def by_item():
            return repository.list_scans_for_library_item(db, jellyfin_item_id="item")

        original_finding_id = db.query(ScanFindingRow.id).filter_by(scan_id="scan").scalar()
        _add_scans(db, 2, verified_finding_id=original_finding_id)
        small = (_count(db, history), _count(db, by_item))
        _add_scans(db, 10, start=2, verified_finding_id=original_finding_id)
        large = (_count(db, history), _count(db, by_item))

        assert large == small
        db.expire_all()
        history_rows = (
            db.query(ScanRow)
            .filter(ScanRow.status == "complete")
            .order_by(ScanRow.updated_at.desc(), ScanRow.created_at.desc())
            .all()
        )
        expected_history = _lazy(db, history_rows)
        db.expire_all()
        assert [scan.model_dump_json() for scan in history()] == expected_history
        db.expire_all()
        item_rows = [db.get(ScanRow, scan.id) for scan in by_item()]
        db.expire_all()
        expected_item = _lazy(db, item_rows)
        db.expire_all()
        scans = by_item()
        assert [scan.model_dump_json() for scan in scans] == expected_item
        assert len(scans) == 14 and scans[-1].id == "associated"
        copied = next(scan for scan in scans if scan.id == "scan-0")
        assert copied.verification_coverage.covered_finding_ids == {
            "audio": [original_finding_id],
            "video": [],
        }
        approvals = {
            scan.id: [approval.model_dump() for approval in scan.community_approvals]
            for scan in scans
        }
        assert approvals["scan-0"] == []
        assert approvals["scan-1"] == [
            {
                "source": "jellyfin",
                "target_item_id": "item",
                "media_source_id": None,
                "channels": ["audio"],
            }
        ]
    finally:
        db.close()


def test_snapshot_findings_verifies_target_once_per_membership():
    db = review_db()
    try:
        # scan-1 holds verified community bytes, so each lookup reaches the target row.
        _add_scans(db, 2, findings=1)
        membership = ReviewScanMembershipRow(
            scan_id="scan-1", role="audio", position=0, scan=db.get(ScanRow, "scan-1")
        )
        db.expire_all()
        with statement_count(db) as few:
            one = _snapshot_findings(db, {"scan-1": membership}, media_item_id="media")
        db.add_all(_finding("scan-1", float(position)) for position in range(5, 25))
        db.commit()
        db.expire_all()
        with statement_count(db) as many:
            snapshots = _snapshot_findings(db, {"scan-1": membership}, media_item_id="media")

        assert len(one) == 1 and len(snapshots) == 21
        assert len(many) == len(few)
    finally:
        db.close()


def _add_reviews(db: Session, start: int, count: int) -> None:
    for index in range(start, start + count):
        review_id = f"review-{index:03}"
        db.add(
            SavedReviewRow(
                id=review_id,
                media_item_id="media",
                media_source_id="",
                name=f"Review {index}",
                updated_at=_BASE + timedelta(minutes=index),
            )
        )
        if index % 2:
            db.flush()
            db.add_all(
                ReviewScanRepairRow(
                    review_id=review_id,
                    missing_scan_id=missing,
                    detected_at=_BASE + timedelta(hours=index),
                    preserved_json=json.dumps({"review_filters": [{}] * index}),
                )
                for missing in ("gone-b", "gone-a")
            )
    db.commit()


def test_saved_review_list_does_not_scale_queries_with_review_count():
    db = review_db()
    try:
        _add_scans(db, 1)
        export = MediaItemExportRow(media_item_id="media", media_source_id="", review_id=None)
        db.add(export)
        db.flush()
        db.add(MediaItemExportScanRow(media_item_edl_id=export.id, scan_id="scan-0", role="audio"))
        _add_reviews(db, 0, 2)
        db.expire_all()
        with statement_count(db) as few:
            repository.list_saved_reviews(db, media_item_id="media")
        _add_reviews(db, 2, 10)
        db.expire_all()
        with statement_count(db) as many:
            summaries = repository.list_saved_reviews(db, media_item_id="media")
        db.expire_all()
        expected = [
            repository._review_summary(db, row).model_dump_json()
            for row in db.query(SavedReviewRow)
            .order_by(SavedReviewRow.updated_at.desc(), SavedReviewRow.id)
            .all()
        ]

        assert len(summaries) == 12
        assert summaries[0].repair is not None and summaries[1].repair is None
        assert len(many) == len(few)
        assert [summary.model_dump_json() for summary in summaries] == expected
    finally:
        db.close()
