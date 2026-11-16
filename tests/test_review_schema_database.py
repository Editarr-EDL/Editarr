"""Fresh-schema and wire-contract checks; never use a runtime database."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import insert, inspect, select
from sqlalchemy.exc import IntegrityError

from scanner.db import (
    Base,
    CreditsDetectionRunRow,
    FilterSelectionRuleRow,
    FilterSettingsRow,
    MediaItemExportRow,
    MediaItemExportScanRow,
    ReviewAcceptedCreditsRow,
    ReviewFilterRow,
    ReviewMcfDocumentRow,
    ReviewScanMembershipRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanMcfDocumentRow,
    ScanRow,
)
from scanner.models import (
    MAX_PERSISTED_INTEGER,
    AcceptedCreditsProvenance,
    AcceptedCreditsReference,
    CreditsCandidate,
    CreditsDetectionResult,
    CreditsEvidence,
    ExportFormat,
    McfCategoryFamily,
    McfChannel,
    McfSourceVersion,
    ReviewSynchronization,
    ScanMode,
    SynchronizationEndMode,
)
from tests.review_schema_support import session as session
from tests.review_schema_support import snapshot


def test_fresh_schema_and_defaults_keep_existing_tables(session):
    inspector = inspect(session.bind)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables)
    assert {"users", "auth_settings", "auth_sessions", "scan_queue", "path_mappings",
            "jellyfin_settings", "arr_settings", "filter_settings", "media_items",
            "saved_reviews", "review_scan_memberships", "review_filters"} <= set(
                inspector.get_table_names()
            )
    assert {"name", "origin", "import_source_name", "imported_at"} <= {
        column["name"] for column in inspector.get_columns("scans")
    }
    assert {"review_id", "exported_format_revision", "format"} <= {
        column["name"] for column in inspector.get_columns("media_item_exports")
    }
    assert {"subcategory", "severity", "mcf_channel", "mcf_comment"} <= {
        column["name"] for column in inspector.get_columns("scan_findings")
    }
    assert {"edl_content_revision", "mcf_content_revision"} <= {
        column["name"] for column in inspector.get_columns("saved_reviews")
    }
    assert {
        "scan_mcf_documents",
        "review_mcf_documents",
        "review_synchronizations",
        "review_accepted_credits",
        "credits_detection_runs",
    } <= set(
        inspector.get_table_names()
    )
    assert "scan_mode" in {
        column["name"] for column in inspector.get_columns("scans")
    }
    assert inspector.get_pk_constraint("review_filters")["constrained_columns"] == [
        "review_id", "id"
    ]
    assert inspector.get_pk_constraint("review_scan_memberships")["constrained_columns"] == [
        "review_id", "scan_id"
    ]
    for review in session.scalars(select(SavedReviewRow)):
        assert (review.name, review.media_source_id, review.revision, review.content_revision) == (
            "Duplicate", "", 0, 0
        )
    assert session.get(ScanRow, "scan-1").origin == "generated"
    assert session.get(ScanRow, "scan-1").scan_mode == ScanMode.CONTENT
    assert session.get(ScanRow, "scan-2").name == "Duplicate"
    assert session.get(ScanRow, "scan-1").imported_at is None


def test_timing_and_credits_schema_constraints_and_cascades(session):
    synchronization = ReviewSynchronizationRow(
        review_id="review-1",
        start_seconds=0,
        end_seconds=100,
        end_mode=SynchronizationEndMode.FILE_END,
    )
    detection = CreditsDetectionRunRow(
        scan_id="scan-1",
        configuration_json='{"detector_version":"v1"}',
        detector_version="v1",
    )
    accepted = ReviewAcceptedCreditsRow(
        review_id="review-1",
        scan_id="scan-1",
        source_finding_id=1,
        candidate_id="candidate-1",
        detected_boundary_seconds=90,
        accepted_boundary_seconds=91,
        confidence=0.8,
        method="hybrid",
        accepted_at=datetime.now(timezone.utc),
    )
    session.add_all([synchronization, detection, accepted])
    session.commit()

    session.delete(session.get(SavedReviewRow, "review-1"))
    session.commit()
    assert session.get(ReviewSynchronizationRow, "review-1") is None
    assert session.get(ReviewAcceptedCreditsRow, "review-1") is None
    assert session.get(CreditsDetectionRunRow, "scan-1") is detection

    with pytest.raises(IntegrityError):
        session.add(ReviewSynchronizationRow(
            review_id="review-2", start_seconds=10, end_seconds=10,
            end_mode="before_credits",
        ))
        session.commit()
    session.rollback()


def test_timing_and_accepted_credits_models_are_strict_and_finite():
    synchronization = ReviewSynchronization(
        start_seconds=0, end_seconds=100, end_mode="file_end"
    )
    reference = AcceptedCreditsReference(
        scan_id="scan-1", candidate_id="candidate-1", accepted_boundary_seconds=91
    )
    provenance = AcceptedCreditsProvenance(
        **reference.model_dump(),
        source_finding_id=1,
        detected_boundary_seconds=90,
        confidence=0.8,
        method="hybrid",
        accepted_at=datetime.now(timezone.utc),
    )
    assert synchronization.end_mode == SynchronizationEndMode.FILE_END
    assert provenance.accepted_boundary_seconds == 91
    for fields in (
        {"start_seconds": -1, "end_seconds": 100, "end_mode": "file_end"},
        {"start_seconds": 10, "end_seconds": 10, "end_mode": "before_credits"},
        {"start_seconds": 0, "end_seconds": float("inf"), "end_mode": "file_end"},
    ):
        with pytest.raises(ValidationError):
            ReviewSynchronization(**fields)
    for source_finding_id in (0, "1", MAX_PERSISTED_INTEGER + 1):
        with pytest.raises(ValidationError):
            AcceptedCreditsProvenance(
                **reference.model_dump(),
                source_finding_id=source_finding_id,
                detected_boundary_seconds=90,
                confidence=0.8,
                method="hybrid",
                accepted_at=datetime.now(timezone.utc),
            )


def test_credits_candidate_result_enforces_confidence_band_and_outcome_shape():
    evidence = CreditsEvidence(
        kind="chapter_marker",
        timestamp_seconds=90,
        score=0.9,
        detail_code="credits_chapter",
        details="Credits",
    )
    candidate = CreditsCandidate(
        id="candidate-1",
        boundary_seconds=90,
        range_start_seconds=84,
        range_end_seconds=96,
        confidence=0.8,
        confidence_band="high",
        method="hybrid",
        evidence=[evidence],
    )
    assert CreditsDetectionResult(
        outcome="candidate",
        candidates=[candidate],
        best_candidate_id=candidate.id,
    ).best_candidate_id == candidate.id
    assert CreditsDetectionResult(
        outcome="no_candidate", candidates=[], best_candidate_id=None
    ).candidates == []
    with pytest.raises(ValidationError, match="confidence band"):
        CreditsCandidate(
            **(candidate.model_dump() | {"confidence_band": "low"})
        )
    with pytest.raises(ValidationError, match="identify the first"):
        CreditsDetectionResult(
            outcome="candidate", candidates=[candidate], best_candidate_id="other"
        )


def test_mcf_classification_pair_constraints_and_models(session):
    with pytest.raises(IntegrityError):
        session.execute(
            insert(ScanFindingRow).values(
                scan_id="scan-1", category="language", subcategory=None,
                start_seconds=3, end_seconds=4, confidence=1, source="audio",
                edit_action="mute",
            )
        )
        session.commit()
    session.rollback()
    with pytest.raises(IntegrityError):
        session.execute(
            insert(ScanFindingRow).values(
                scan_id="scan-1", category="language", subcategory="violence",
                start_seconds=3, end_seconds=4, confidence=1, source="audio",
                edit_action="mute",
            )
        )
        session.commit()
    session.rollback()

    finding = ScanFindingRow(
        scan_id="scan-1", category=McfCategoryFamily.LANGUAGE, subcategory="swearing",
        severity="medium", mcf_channel=McfChannel.AUDIO, start_seconds=3,
        end_seconds=4, confidence=1, source="audio", edit_action="mute",
    )
    session.add(finding)
    session.commit()
    assert finding.category == McfCategoryFamily.LANGUAGE
    assert finding.subcategory == "swearing"


def test_mcf_metadata_isolation_and_review_delete_cascade(session):
    source = ScanMcfDocumentRow(
        scan_id="scan-1", source_version=McfSourceVersion.V1_1_0,
        title="Source title", material_start_seconds=0, material_end_seconds=100,
    )
    review = ReviewMcfDocumentRow(
        review_id="review-1", title=source.title,
        material_start_seconds=source.material_start_seconds,
        material_end_seconds=source.material_end_seconds,
    )
    session.add_all([source, review])
    session.commit()
    review.title = "Edited title"
    session.commit()
    assert source.title == "Source title"

    session.delete(session.get(SavedReviewRow, "review-1"))
    session.commit()
    assert session.get(ReviewMcfDocumentRow, "review-1") is None
    assert session.get(ScanMcfDocumentRow, "scan-1") is not None


def test_filter_settings_have_stable_ordered_medium_defaults(session):
    settings = session.get(FilterSettingsRow, 1)
    assert settings is not None
    assert settings.revision == 0
    assert settings.selection_rules == []
    assert len(settings.severity_defaults) == 97
    assert [item.position for item in settings.severity_defaults] == list(range(97))
    assert settings.severity_defaults[0].subcategory == "commercial"
    assert settings.severity_defaults[-1].subcategory == "weapons"
    assert {item.severity.value for item in settings.severity_defaults} == {"medium"}

    rule_id = str(uuid4())
    settings.selection_rules.append(
        FilterSelectionRuleRow(
            id=rule_id, position=0, disposition="deny", category="language"
        )
    )
    session.commit()
    assert session.get(FilterSelectionRuleRow, rule_id).position == 0

    with pytest.raises(IntegrityError):
        session.execute(
            insert(FilterSelectionRuleRow).values(
                id=str(uuid4()), settings_id=1, position=1, disposition="allow",
                category="language", subcategory="violence",
            )
        )
        session.commit()
    session.rollback()


@pytest.mark.parametrize("name", ["", " ", "\u2003", "x" * 101, "x\n", "x\x00y", "x\x85y"])
@pytest.mark.parametrize("row", [SavedReviewRow, ScanRow])
def test_database_rejects_invalid_names_even_without_orm_validation(session, name, row):
    with pytest.raises(IntegrityError):
        session.execute(row.__table__.update().values(name=name))
        session.commit()
    session.rollback()


@pytest.mark.parametrize(
    "field", ["revision", "content_revision", "edl_content_revision", "mcf_content_revision"]
)
def test_database_rejects_negative_revisions(session, field):
    with pytest.raises(IntegrityError):
        session.execute(SavedReviewRow.__table__.update().values({field: -1}))
    session.rollback()


def test_database_rejects_invalid_origin_role_position_and_membership_duplicates(session):
    with pytest.raises(IntegrityError):
        session.execute(ScanRow.__table__.update().values(origin="inferred"))
    session.rollback()
    for role, position in [("visual", 0), ("both", -1)]:
        with pytest.raises(IntegrityError):
            session.execute(insert(ReviewScanMembershipRow).values(
                review_id="review-1", scan_id="scan-1", role=role, position=position
            ))
        session.rollback()
    session.add(ReviewScanMembershipRow(
        review_id="review-1", scan_id="scan-1", role="both", position=0
    ))
    session.commit()
    for scan_id, position in [("scan-1", 1), ("scan-2", 0)]:
        with pytest.raises(IntegrityError):
            session.execute(insert(ReviewScanMembershipRow).values(
                review_id="review-1", scan_id=scan_id, role="audio", position=position
            ))
        session.rollback()


def test_filter_provenance_uniqueness_and_review_local_identity(session):
    filter_id = str(uuid4())
    session.add(ReviewFilterRow(**snapshot(id=filter_id)))
    session.commit()
    for changes in [dict(position=1), dict(source_scan_id="scan-2", position=1)]:
        with pytest.raises(IntegrityError):
            session.execute(insert(ReviewFilterRow).values(**snapshot(**changes)))
        session.rollback()
    # Same filter UUID and same evidence can appear in a different review.
    session.add(ReviewFilterRow(**snapshot(review_id="review-2", id=filter_id)))
    # NULL staged provenance does not deduplicate distinct filters.
    session.add_all([ReviewFilterRow(**snapshot(source_finding_id=None, position=position))
                     for position in (1, 2)])
    session.commit()


def test_review_owned_content_and_delete_cascade_preserve_evidence_and_edl(session):
    review = session.get(SavedReviewRow, "review-1")
    review.memberships.append(ReviewScanMembershipRow(scan_id="scan-1", role="audio", position=0))
    review.filters.append(ReviewFilterRow(**snapshot()))
    edl = MediaItemExportRow(
        media_item_id="media", review_id=review.id, exported_format_revision=0,
        scans=[MediaItemExportScanRow(scan_id="scan-1", role="audio")],
    )
    session.add(edl)
    session.commit()
    review.filters[0].start_seconds = 1.5
    review.filters[0].label = "Edited"
    session.commit()
    assert session.get(ScanFindingRow, 1).start_seconds == 1
    assert session.get(ScanFindingRow, 1).label == "Evidence"
    # Review deletion cannot silently remove or clear EDL provenance.
    with pytest.raises(IntegrityError):
        session.delete(review)
        session.commit()
    session.rollback()
    edl.review = None
    edl.exported_format_revision = None
    session.flush()
    session.delete(review)
    session.commit()
    assert session.scalar(select(ReviewFilterRow)) is None
    assert session.scalar(select(ReviewScanMembershipRow)) is None
    assert session.get(ScanFindingRow, 1) is not None
    assert session.get(ScanRow, "scan-1") is not None
    assert session.get(SavedReviewRow, "review-2") is not None
    assert session.get(MediaItemExportRow, edl.id).review_id is None
    assert len(edl.scans) == 1


def test_referenced_scan_cannot_cascade_through_review(session):
    session.add(ReviewScanMembershipRow(
        review_id="review-1", scan_id="scan-2", role="video", position=0
    ))
    session.commit()
    with pytest.raises(IntegrityError):
        session.delete(session.get(ScanRow, "scan-2"))
        session.commit()
    session.rollback()
    assert session.get(SavedReviewRow, "review-1") is not None


@pytest.mark.parametrize("review_id,revision", [(None, 0), ("review-1", None), ("review-1", -1)])
def test_export_provenance_requires_a_nonnegative_pair(session, review_id, revision):
    session.add(MediaItemExportRow(
        media_item_id="media", review_id=review_id, exported_format_revision=revision
    ))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_edl_and_mcf_export_rows_coexist_for_one_version(session):
    session.add_all(
        [
            MediaItemExportRow(media_item_id="media", format=ExportFormat.EDL),
            MediaItemExportRow(media_item_id="media", format=ExportFormat.MCF),
        ]
    )
    session.commit()
    assert {row.format for row in session.scalars(select(MediaItemExportRow))} == {
        ExportFormat.EDL,
        ExportFormat.MCF,
    }


