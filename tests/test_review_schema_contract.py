"""Fresh-schema and wire-contract checks; never use a runtime database."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import (
    Base,
    FilterSettingsRow,
)
from scanner.models import (
    MAX_PERSISTED_INTEGER,
    EdlImportOptions,
    EdlImportWarning,
    ExportFormat,
    ExportSummary,
    LibraryReviewCreate,
    McfCategoryFamily,
    McfMetadataSeedCandidate,
    NewManualReviewFilter,
    ReviewCreate,
    ReviewDuplicate,
    ReviewExportRequest,
    ReviewFilter,
    ReviewFilterInput,
    ReviewMcfDocument,
    ReviewRename,
    ReviewRetimeRequest,
    ReviewRetimeResult,
    ReviewSave,
    ReviewScanRole,
    ReviewSource,
    ReviewTargetRequest,
    SavedReview,
    ScanNameUpdate,
    ScanOrigin,
)
from tests.review_schema_support import filter_input
from tests.review_schema_support import session as session


def test_fresh_schema_seeds_survive_file_backed_restart(tmp_path):
    database_path = tmp_path / "restart.db"
    engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    with Session(engine) as first:
        settings = first.get(FilterSettingsRow, 1)
        assert settings is not None
        assert settings.revision == 0
        assert len(settings.severity_defaults) == 105
    engine.dispose()

    reopened = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(reopened)
    with Session(reopened) as second:
        settings = second.get(FilterSettingsRow, 1)
        assert settings is not None
        assert settings.severity_defaults[0].subcategory == "commercial"
        assert settings.severity_defaults[-1].subcategory == "abuse"
    reopened.dispose()


@pytest.mark.parametrize("name", ["\nName", "Name\r", "Name\x00", "Name\x85", "\ud800", "x" * 101])
def test_names_reject_controls_surrogates_and_overlong_values(name):
    for dto in (ScanNameUpdate, ReviewCreate):
        with pytest.raises(ValidationError):
            dto(name=name)


def test_names_trim_without_casefolding_or_unicode_normalization():
    name = "\u2003CaFe\u0301 😀\u00a0"
    assert ScanNameUpdate(name=name).name == "CaFe\u0301 😀"
    assert ReviewCreate(name=name).name == "CaFe\u0301 😀"
    assert ReviewCreate(name="😀" * 100).name == "😀" * 100
    assert ScanNameUpdate(name=" \u2003").name is None
    assert ScanNameUpdate(name=None).name is None
    for value in (None, "", " \u2003"):
        with pytest.raises(ValidationError):
            ReviewCreate(name=value)
    with pytest.raises(ValidationError):
        ScanNameUpdate()


@pytest.mark.parametrize("dto", [ReviewRename, ReviewDuplicate, ReviewExportRequest])
@pytest.mark.parametrize("revision", [-1, True, 1.5, "0"])
def test_revision_inputs_require_nonnegative_integers(dto, revision):
    payload = {"revision": revision}
    if dto != ReviewExportRequest:
        payload["name"] = "Review"
    with pytest.raises(ValidationError):
        dto(**payload)


@pytest.mark.parametrize("changes", [
    {"start_seconds": -1}, {"end_seconds": 1}, {"end_seconds": float("inf")},
    {"start_seconds": float("nan")}, {"confidence": -0.1}, {"confidence": 1.1},
    {"confidence": float("nan")}, {"source": "video"}, {"edit_action": "cut"}, {"id": "bad"},
    {"category": "language", "subcategory": "violence"},
    {"category": "language", "subcategory": None},
    {"source_finding_id": 0},
    {"source_finding_id": "1"},
    {"source_finding_id": MAX_PERSISTED_INTEGER + 1},
])
def test_filter_write_validation(changes):
    with pytest.raises(ValidationError):
        ReviewFilterInput(**filter_input(**changes))


def test_staged_manual_input_has_no_fake_provenance_and_requires_end():
    values = filter_input(
        category=McfCategoryFamily.LANGUAGE, subcategory="swearing"
    )
    for key in ("source_scan_id", "source_finding_id", "confidence", "selected"):
        values.pop(key)
    result = NewManualReviewFilter(**values)
    assert result.confidence is None and result.selected
    for changes in ({"end_seconds": None}, {"source_scan_id": "fake"}):
        with pytest.raises(ValidationError):
            NewManualReviewFilter(**(values | changes))
    with pytest.raises(ValidationError):
        NewManualReviewFilter(
            **{key: value for key, value in values.items() if key != "end_seconds"}
        )
    assert ReviewFilterInput(**filter_input(end_seconds=None)).end_seconds is None


def test_import_catalog_target_rejects_client_path():
    source = "jellyfin"
    options = EdlImportOptions(target={"source": source, "item_id": "1"}, name=" Import ")
    assert options.name == "Import"
    assert options.target.media_source_id is None
    with pytest.raises(ValidationError):
        EdlImportOptions(target={"source": source, "item_id": "1", "video_path": "/file"}, name="N")


def test_review_target_requires_a_jellyfin_item():
    adapter = TypeAdapter(ReviewTargetRequest)
    for payload in (
        {"source": "jellyfin"},
        {"source": "jellyfin", "item_id": ""},
        {"source": "jellyfin", "item_id": "1", "video_path": "/file"},
        {"source": "manual", "item_id": "1"},
        {"source": "manual", "video_path": "/file"},
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(payload)
    target = adapter.validate_python(
        {"source": "jellyfin", "item_id": "1", "media_source_id": "version"}
    )
    assert (target.item_id, target.media_source_id) == ("1", "version")


def test_all_new_write_models_forbid_unknown_fields_and_create_defaults_are_independent():
    payloads = [
        (ScanNameUpdate, {"name": None}), (ReviewCreate, {"name": "N"}),
        (
            ReviewSave,
            {
                "name": "N",
                "revision": 0,
                "memberships": [],
                "filters": [],
                "mcf_document": {},
            },
        ),
        (ReviewRename, {"name": "N", "revision": 0}),
        (ReviewDuplicate, {"name": "N", "revision": 0}),
        (ReviewExportRequest, {"revision": 0}),
        (ReviewFilterInput, filter_input()),
        (LibraryReviewCreate, {"name": "N"}),
        (EdlImportOptions, {"name": "N", "target": {"source": "jellyfin", "item_id": "1"}}),
    ]
    for dto, payload in payloads:
        with pytest.raises(ValidationError):
            dto(**payload, unexpected=True)
    first, second = ReviewCreate(name="N"), ReviewCreate(name="N")
    first.memberships.append({"scan_id": "1", "role": "both"})
    assert second.memberships == [] and second.filters is None
    with pytest.raises(ValidationError, match="mcf_document"):
        ReviewSave(name="N", revision=0, memberships=[], filters=[])
    with pytest.raises(ValidationError, match="synchronization"):
        ReviewSave(
            name="N", revision=0, memberships=[], filters=[], mcf_document={}
        )


def test_response_serialization_and_timezone_contract():
    now = datetime.now(timezone.utc)
    review = SavedReview(
        id=str(uuid4()), media_item_id="media", target={"source": "jellyfin", "item_id": "1",
                                                     "media_source_id": ""},
        name="N", revision=0, content_revision=0, created_at=now, updated_at=now,
        memberships=[], scans=[], filters=[ReviewFilter(**filter_input())],
        synchronization={"start_seconds": 0, "end_seconds": 100, "end_mode": "file_end"},
    )
    encoded = review.model_dump(mode="json")
    assert encoded["created_at"].endswith("Z")
    assert encoded["target"] == {"source": "jellyfin", "item_id": "1", "media_source_id": None}
    assert encoded["filters"][0]["source"] == "audio"
    assert encoded["filters"][0]["category"] is None
    assert encoded["filters"][0]["subcategory"] is None
    with pytest.raises(ValidationError):
        SavedReview(**(review.model_dump() | {"created_at": now.replace(tzinfo=None)}))
    with pytest.raises(ValidationError):
        ExportSummary(format=ExportFormat.EDL, exported_at=now.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        EdlImportWarning(
            code="unsupported_actions_ignored", message="Ignored", ignored_action_count=0
        )
    assert {member.value for member in ScanOrigin} == {"generated", "manual", "imported"}
    assert {member.value for member in ReviewSource} == {"jellyfin"}
    assert {member.value for member in ReviewScanRole} == {"audio", "video", "both"}


def test_openapi_has_new_scan_review_and_embedded_provenance_fields():
    from scanner.api import app

    schema = app.openapi()
    assert set(schema["paths"]["/api/reviews/{review_id}"]) >= {
        "get", "put", "patch", "delete"
    }
    retime = schema["paths"]["/api/reviews/{review_id}/retime"]["post"]
    assert retime["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ReviewRetimeRequest")
    assert retime["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ReviewRetimeResult")
    assert {"404", "409", "422", "502"} <= set(retime["responses"])
    assert not {"/api/edl-imports/path", "/api/mcf-imports/path"} & set(schema["paths"])
    assert schema["paths"]["/api/mcf-imports/upload"]["post"]["responses"]["201"][
        "content"
    ]["application/json"]["schema"]["$ref"].endswith("/McfImportResult")
    assert schema["paths"]["/api/reviews/{review_id}/mcf/preflight"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/McfPreflight"
    )
    assert schema["paths"]["/api/reviews/{review_id}/mcf"]["post"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReviewExportResponse"
    )
    definitions = schema["components"]["schemas"]
    assert {"mcf_document", "synchronization"} <= set(
        definitions["ReviewSave"]["required"]
    )
    assert {"synchronization", "accepted_credits"} <= set(
        definitions["SavedReview"]["properties"]
    )
    assert {"scan_mode", "credits_detection"} <= set(
        definitions["Scan"]["properties"]
    )
    assert {"scan_id", "candidate_id"} <= set(
        definitions["ReviewErrorDetail"]["properties"]
    )
    accept_responses = schema["paths"][
        "/api/credits-detections/{scan_id}/accept"
    ]["post"]["responses"]
    assert {"400", "404", "409", "422", "500", "503"} <= set(
        accept_responses
    )
    for status in (400, 404, 409, 422, 500, 503):
        assert accept_responses[str(status)]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("/ReviewError")
    assert {
        "ReviewSynchronization",
        "AcceptedCreditsReference",
        "AcceptedCreditsProvenance",
        "CreditsDetection",
        "CreditsDetectionConfiguration",
        "CreditsDetectionResult",
        "CreditsCandidate",
        "CreditsEvidence",
        ReviewRetimeRequest.__name__,
        ReviewRetimeResult.__name__,
    } <= set(definitions)
    assert set(definitions["ReviewRetimeRequest"]["required"]) == {
        "revision",
        "memberships",
        "current_synchronization",
        "proposed_synchronization",
        "mcf_document",
        "filters",
        "accepted_credits",
        "new_manual_filters",
    }
    assert "accepted_credits" in definitions["ReviewRetimeResult"]["required"]
    assert "mcf_metadata_seed" in definitions["LibraryReviewCreate"]["properties"]
    assert "ManualFileReviewCreate" not in definitions
    assert "metadata_candidates" in definitions["ReviewErrorDetail"]["properties"]
    for model in ("Scan", "ScanQueueItem"):
        assert {"name", "origin", "import_source_name", "imported_at"} <= set(
            definitions[model]["properties"]
        )
    for model in ("JellyfinMediaItem", "JellyfinMediaVersion"):
        assert "exports" in definitions[model]["properties"]
        assert not {
            "has_exported_edl",
            "exported_at",
            "exported_scans",
            "review_id",
            "exported_content_revision",
            "exported_review",
        } & set(definitions[model]["properties"])
    assert "EdlStatus" not in definitions
    assert "ReviewEdlExport" not in definitions

    assert schema["paths"]["/api/library-items/{item_id}/exports"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"].endswith("/ExportStatus")
    responses = schema["paths"]["/api/library-items/{item_id}/exports/{format}"]["delete"][
        "responses"
    ]
    for status in (404, 500):
        assert responses[str(status)]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/ReviewError")
    assert not any(path.startswith("/api/manual-") for path in schema["paths"])

    review_operations = [
        ("/api/library-items/{item_id}/reviews", "post", {409, 502}),
        ("/api/reviews/{review_id}", "get", {404}),
        ("/api/reviews/{review_id}", "put", {404, 409, 502}),
        ("/api/reviews/{review_id}", "patch", {404, 409}),
        ("/api/reviews/{review_id}", "delete", {404, 409}),
        ("/api/reviews/{review_id}/duplicate", "post", {404, 409, 502}),
        ("/api/reviews/{review_id}/edl", "post", {404, 409, 502}),
        ("/api/reviews/{review_id}/mcf/preflight", "get", {404, 409, 502}),
        ("/api/reviews/{review_id}/mcf", "post", {404, 409, 422, 500, 502}),
    ]
    for path, method, statuses in review_operations:
        responses = schema["paths"][path][method]["responses"]
        for status in statuses:
            response_schema = responses[str(status)]["content"]["application/json"]["schema"]
            assert response_schema["$ref"].endswith("/ReviewError")

    for path, statuses in (
        ("/api/edl-imports/upload", {400, 409, 413, 422, 502}),
        ("/api/mcf-imports/upload", {400, 409, 413, 422, 502}),
    ):
        responses = schema["paths"][path]["post"]["responses"]
        for status in statuses - {422}:
            response_schema = responses[str(status)]["content"]["application/json"]["schema"]
            assert response_schema["$ref"].endswith("/ReviewError")
        validation_or_domain = responses["422"]["content"]["application/json"]["schema"]
        assert {
            alternative["$ref"].rsplit("/", 1)[-1]
            for alternative in validation_or_domain["anyOf"]
        } == {"ReviewError", "RequestValidationErrorResponse"}


def test_metadata_source_conflict_maps_to_safe_candidate_response():
    from scanner import api, repository

    candidates = [
        McfMetadataSeedCandidate(
            kind="scan",
            scan_id="scan-1",
            label="Scan 1",
            mcf_document=ReviewMcfDocument(title="Imported"),
        ),
        McfMetadataSeedCandidate(
            kind="catalog",
            label="Catalog/runtime defaults",
            mcf_document=ReviewMcfDocument(title="Catalog"),
        ),
    ]

    error = api._review_mutation_error(repository.McfMetadataSourceRequired(candidates))

    assert error.status_code == 409
    assert error.detail["code"] == "mcf_metadata_source_required"
    assert [item["kind"] for item in error.detail["metadata_candidates"]] == [
        "scan",
        "catalog",
    ]
