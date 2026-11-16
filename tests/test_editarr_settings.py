from datetime import datetime, timezone
from types import SimpleNamespace

from scanner import api, repository
from scanner.models import (
    ExportFormat,
    ExportStatus,
    ExportSummary,
    JellyfinMediaItem,
    JellyfinMediaVersion,
    JellyfinSettingsUpdate,
    ReviewTarget,
    SavedReviewSummary,
)
from tests.editarr_support import db as db


def test_jellyfin_settings_are_write_only_and_connection_state_is_safe(db):
    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://jellyfin.example/base/", api_key="super-secret"),
    )

    assert repository.get_jellyfin_credentials(db) == (
        "http://jellyfin.example/base/",
        "super-secret",
    )
    assert repository.get_jellyfin_settings(db).model_dump() == {
        "server_url": "http://jellyfin.example/base/",
        "api_key_configured": True,
        "last_checked_at": None,
        "last_error": None,
    }

    repository.record_jellyfin_connection(db, "Jellyfin rejected the configured API key")
    safe_settings = repository.get_jellyfin_settings(db)
    assert safe_settings.last_checked_at is not None
    assert safe_settings.last_error == "Jellyfin rejected the configured API key"
    assert "super-secret" not in str(safe_settings.model_dump())


def test_disconnect_clears_jellyfin_credentials_and_catalog_caches(db):
    repository.update_jellyfin_settings(
        db,
        JellyfinSettingsUpdate(server_url="http://jellyfin.example", api_key="jellyfin-secret"),
    )
    api.LIBRARY_ITEMS_CACHE["movies"] = (0.0, [])
    api.JELLYFIN_ITEM_CACHE["movie-1"] = (
        0.0,
        JellyfinMediaItem(
            id="movie-1",
            library_id="movies",
            item_type="Movie",
            name="Example",
        ),
    )
    caller = SimpleNamespace(stage=lambda *_args, **_kwargs: None)
    jellyfin = api.api_disconnect_jellyfin(db, caller)

    assert jellyfin.server_url is None
    assert jellyfin.api_key_configured is False
    assert repository.get_jellyfin_credentials(db) is None
    assert api.LIBRARY_ITEMS_CACHE == {}
    assert api.JELLYFIN_ITEM_CACHE == {}


def test_catalog_item_state_preserves_saved_review_provenance(monkeypatch, db):
    now = datetime.now(timezone.utc)
    review = SavedReviewSummary(
        id="review-1",
        media_item_id="media-1",
        target=ReviewTarget(source="jellyfin", item_id="movie-1", media_source_id="source-4k"),
        name="Review",
        revision=2,
        content_revision=2,
        edl_content_revision=2,
        mcf_content_revision=0,
        created_at=now,
        updated_at=now,
    )
    status = ExportStatus(
        exports={
            ExportFormat.EDL: ExportSummary(
                format=ExportFormat.EDL,
                present=True,
                exported_at=now,
                review_id=review.id,
                exported_format_revision=1,
                outdated=True,
                exported_review=review,
            ),
            ExportFormat.MCF: ExportSummary(format=ExportFormat.MCF),
        }
    )
    monkeypatch.setattr(
        api,
        "library_item_export_statuses",
        lambda _db, *, server_id, jellyfin_item_ids: {
            item_id: status for item_id in jellyfin_item_ids
        },
    )
    monkeypatch.setattr(
        api,
        "library_item_version_export_statuses_for_items",
        lambda _db, *, server_id, jellyfin_item_ids: {
            item_id: {"source-4k": status} for item_id in jellyfin_item_ids
        },
    )

    jellyfin_item = api._with_library_item_state(
        db,
        server_id="http://jellyfin.test",
        items=[
            JellyfinMediaItem(
                id="movie-1",
                item_type="Movie",
                name="Movie",
                versions=[JellyfinMediaVersion(id="source-4k")],
            )
        ],
    )[0]

    for decorated in (jellyfin_item, jellyfin_item.versions[0]):
        assert decorated.exports == status
        edl = decorated.exports.exports[ExportFormat.EDL]
        assert edl.review_id == review.id
        assert edl.exported_format_revision == 1
        assert edl.exported_review == review
        assert edl.outdated is True
