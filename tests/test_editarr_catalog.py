from pathlib import Path

import pytest

from scanner import api, repository
from scanner.models import (
    ExportFormat,
    ExportStatus,
    FilterDefaultsUpdate,
    FilterRuleDisposition,
    FilterSelectionRule,
    JellyfinSettingsUpdate,
    McfCategoryFamily,
    PathMappingCreate,
)
from tests.editarr_support import db as db
from tests.editarr_support import record_edl_export


def test_openapi_exposes_the_editarr_domain_contract():
    schema = api.app.openapi()
    component_schemas = schema["components"]["schemas"]
    for name in [
        "JellyfinLibrary",
        "JellyfinMediaItem",
        "Scan",
        "ScanFinding",
        "ScanQueueItem",
        "ScanFindings",
        "ReviewExportResponse",
        "PathMapping",
    ]:
        assert name in component_schemas
    assert schema["paths"]["/api/reviews/{review_id}/edl"]["post"]["responses"]["200"]
    assert schema["paths"]["/api/scans/{scan_id}/references"]["get"]
    delete_scan_responses = schema["paths"]["/api/scans/{scan_id}"]["delete"]["responses"]
    assert delete_scan_responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReviewError"
    )
    for removed_path in [
        "/api/scans/{scan_id}/edl",
        "/api/scans/{scan_id}/findings/selection",
        "/api/scans/{scan_id}/findings/{finding_id}",
        "/api/scans/{scan_id}/findings/{finding_id}/mute-preview",
        "/api/scans/{scan_id}/findings/{finding_id}/video-preview",
        "/api/library-items/{item_id}/manual-findings",
        "/api/sources/{service}/items/{item_id}/manual-findings",
        "/api/manual-files/findings",
        "/api/manual-files/exports",
        "/api/manual-files/exports/{format}",
        "/api/manual-files/reviews",
        "/api/manual-files/identity",
        "/api/manual-scans",
    ]:
        assert removed_path not in schema["paths"]
    assert schema["paths"]["/api/libraries/{library_id}/items"]["get"]
    for removed_path in [
        "/api/settings/sources/{service}",
        "/api/sources/{service}/items",
        "/api/sources/{service}/items/{item_id}/reviews",
        "/api/sources/radarr/items/{item_id}/scans",
        "/api/sources/sonarr/episodes/{item_id}/scans",
        "/api/videos",
        "/api/videos/{video_id}",
        "/api/scan",
        "/api/queue",
    ]:
        assert removed_path not in schema["paths"]
    for removed_schema in [
        "Video",
        "VideoCreate",
        "Finding",
        "ScanProgress",
        "VideoScanQueueItem",
    ]:
        assert removed_schema not in component_schemas
    assert set(component_schemas["ReviewExportRequest"]["properties"]) == {
        "revision",
        "policy_owner_id",
        "policy_revision",
    }


def test_capabilities_expose_legacy_choices_and_detailed_backend_records():
    capabilities = api.api_capabilities()
    api.ScanCapabilities.model_validate(capabilities)
    detailed = {capability.id: capability for capability in capabilities["model_capabilities"]}

    assert capabilities["target_id"] == api.current_environment().target_id
    assert {model.id for model in capabilities["audio_models"]} == {
        model_id
        for model_id in api.SUPPORTED_AUDIO_MODELS
        if detailed[model_id].availability == "available"
    }
    assert {model.id for model in capabilities["audio_filters"]} == {
        model_id
        for model_id in api.SUPPORTED_AUDIO_FILTERS
        if detailed[model_id].availability == "available"
    }
    assert {model.id for model in capabilities["video_models"]} == {
        model_id
        for model_id in api.SUPPORTED_VIDEO_MODELS
        if detailed[model_id].availability == "available"
    }
    assert [model.id for model in capabilities["qwen_models"]] == [
        "qwen3-vl-8b-instruct-4bit",
        "qwen3-vl-4b-instruct-4bit",
    ]
    assert detailed["qwen3_vl"].stage == "enrichment"
    assert detailed["qwen3_vl"].weight_requirement == "on_demand"


def test_unavailable_optional_audio_model_is_not_advertised(monkeypatch):
    original = api.model_capabilities()
    monkeypatch.setattr(
        api,
        "model_capabilities",
        lambda _environment=None: tuple(
            capability.model_copy(
                update={"availability": "unavailable", "reason": "missing_package"}
            )
            if capability.id == "mlx_whisper"
            else capability
            for capability in original
        ),
    )

    assert "mlx_whisper" not in [model.id for model in api.api_capabilities()["audio_models"]]


def test_scan_options_preserve_the_legacy_alt_profanity_filter_alias(monkeypatch):
    monkeypatch.setattr(api, "require_available", lambda _backend_id: None)

    assert api.ScanOptions(scan_video=False, audio_filter="alt-profanity-check").audio_filter == (
        "alt-profanity-check"
    )


def test_scan_options_reject_unknown_qwen_model():
    with pytest.raises(ValueError, match="Unsupported Severity and Description model"):
        api.ScanOptions(scan_audio=False, qwen_model="unknown-qwen")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("coarse_scan_interval", float("nan")),
        ("coarse_scan_interval", float("inf")),
        ("qwen_segment_join_gap_seconds", float("nan")),
        ("qwen_segment_join_gap_seconds", float("inf")),
    ],
)
def test_scan_options_reject_non_finite_numeric_values(field, value):
    with pytest.raises(ValueError):
        api.ScanOptions(**{field: value})


def test_default_filter_settings_round_trip(db):
    current = repository.get_structured_filter_defaults_settings(db)
    assert current.selection_rules == []
    updated = repository.update_filter_defaults_settings(
        db,
        FilterDefaultsUpdate(
            revision=current.revision,
            selection_rules=[
                FilterSelectionRule(
                    id="82f1b419-dd1f-48a8-8293-60181c799760",
                    disposition=FilterRuleDisposition.ALLOW,
                    evidence_source="visual",
                    category=McfCategoryFamily.VIOLENCE,
                ),
                FilterSelectionRule(
                    id="45a98f8e-3b04-45a5-a03f-0c54203a540d",
                    disposition=FilterRuleDisposition.DENY,
                    evidence_source="visual",
                    category=McfCategoryFamily.NUDITY,
                ),
                FilterSelectionRule(
                    id="78d32dfa-7693-469a-8af8-da30059cd19d",
                    disposition=FilterRuleDisposition.ALLOW,
                    evidence_source="audio",
                    label_contains=" damn ",
                ),
                FilterSelectionRule(
                    id="fbb3de12-3522-4400-99e8-c8692bd6d986",
                    disposition=FilterRuleDisposition.DENY,
                    evidence_source="audio",
                    label_contains=" hell ",
                ),
            ],
            severity_defaults=current.severity_defaults,
        ),
    )
    assert updated.revision == 1
    assert repository.get_structured_filter_defaults_settings(db) == updated


def test_persisted_jellyfin_settings_are_the_only_runtime_credential_source(monkeypatch, db):
    monkeypatch.setenv("EDITARR_JELLYFIN_SERVER_URL", "http://environment.example")
    monkeypatch.setenv("EDITARR_JELLYFIN_API_KEY", "environment-secret")

    assert repository.get_jellyfin_credentials(db) is None

    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://settings.example", api_key="settings-secret"),
    )

    assert repository.get_jellyfin_credentials(db) == (
        "http://settings.example",
        "settings-secret",
    )


def test_path_mapping_uses_the_most_specific_component_safe_prefix(db, tmp_path: Path):
    general = repository.create_path_mapping(
        db, PathMappingCreate(remote_path="/remote/media", local_path=str(tmp_path / "media"))
    )
    specific = repository.create_path_mapping(
        db,
        PathMappingCreate(remote_path="/remote/media/movies", local_path=str(tmp_path / "movies")),
    )

    resolved = repository.resolve_local_path(db, "/remote/media/movies/Drama/example.mkv")
    assert resolved.mapping_id == specific.id
    assert resolved.local_path == str(tmp_path / "movies" / "Drama" / "example.mkv")

    general_resolution = repository.resolve_local_path(db, "/remote/media/shows/example.mkv")
    assert general_resolution.mapping_id == general.id
    assert general_resolution.local_path == str(tmp_path / "media" / "shows" / "example.mkv")

    no_substring_match = repository.resolve_local_path(db, "/remote/mediabox/example.mkv")
    assert no_substring_match.mapping_id is None
    assert no_substring_match.local_path == "/remote/mediabox/example.mkv"


def test_path_mapping_reorder_requires_the_complete_ordered_set(db, tmp_path: Path):
    first = repository.create_path_mapping(
        db, PathMappingCreate(remote_path="/remote/first", local_path=str(tmp_path / "first"))
    )
    second = repository.create_path_mapping(
        db, PathMappingCreate(remote_path="/remote/second", local_path=str(tmp_path / "second"))
    )

    reordered = repository.reorder_path_mappings(db, [second.id, first.id])
    assert [mapping.id for mapping in reordered] == [
        second.id,
        first.id,
    ]
    with pytest.raises(ValueError, match="every path mapping exactly once"):
        repository.reorder_path_mappings(db, [first.id])


def test_deleting_an_exported_edl_removes_the_sidecar_and_clears_state(db, tmp_path: Path):
    video_path = tmp_path / "example.mkv"
    video_path.write_bytes(b"video")
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id="movie-1",
        library_id="movies",
        item_type="Movie",
        name="Example",
        path="/remote/example.mkv",
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=str(video_path),
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        frame_interval=None,
        speed_up=False,
    )
    edl_path = video_path.with_suffix(".edl")
    edl_path.write_text("0 10 0\n")
    record_edl_export(
        db,
        media_item_id=media_item.id,
        media_source_id="source-1",
        scan_id=scan.id,
    )

    assert (
        repository.delete_library_item_export(
            db,
            server_id="http://jellyfin.test",
            jellyfin_item_id="movie-1",
            media_source_id="source-1",
            export_format=ExportFormat.EDL,
        )
        is True
    )
    assert not edl_path.exists()
    assert (
        repository.library_item_export_statuses(
            db, server_id="http://jellyfin.test", jellyfin_item_ids=["movie-1"]
        )["movie-1"]
        == ExportStatus()
    )


def test_path_mappings_validate_absolute_paths(db, tmp_path: Path):
    with pytest.raises(ValueError, match="Remote path must be an absolute"):
        repository.create_path_mapping(
            db, PathMappingCreate(remote_path="relative/media", local_path=str(tmp_path))
        )
    with pytest.raises(ValueError, match="Local path must be an absolute"):
        repository.create_path_mapping(
            db, PathMappingCreate(remote_path="/remote/media", local_path="relative/media")
        )


def test_path_mapping_supports_windows_remote_paths(db, tmp_path: Path):
    mapping = repository.create_path_mapping(
        db,
        PathMappingCreate(
            remote_path=r"Z:\Media\Movies",
            local_path=str(tmp_path / "movies"),
        ),
    )

    resolution = repository.resolve_local_path(db, r"Z:\Media\Movies\Example\movie.mkv")
    assert resolution.mapping_id == mapping.id
    assert resolution.local_path == str(tmp_path / "movies" / "Example" / "movie.mkv")
