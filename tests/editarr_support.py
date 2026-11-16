from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner import api
from scanner.db import (
    Base,
    MediaItemExportRow,
    MediaItemExportScanRow,
)


@pytest.fixture
def db():
    api.LIBRARY_ITEMS_CACHE.clear()
    api.JELLYFIN_ITEM_CACHE.clear()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        api.LIBRARY_ITEMS_CACHE.clear()
        api.JELLYFIN_ITEM_CACHE.clear()


def record_edl_export(
    db,
    *,
    media_item_id: str,
    media_source_id: str,
    scan_id: str,
    role: str = "audio",
) -> MediaItemExportRow:
    export = MediaItemExportRow(
        media_item_id=media_item_id,
        media_source_id=media_source_id,
    )
    db.add(export)
    db.flush()
    db.add(
        MediaItemExportScanRow(
            media_item_edl_id=export.id,
            scan_id=scan_id,
            role=role,
        )
    )
    db.commit()
    return export


def _isolate_api_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Patch the shared app settings before ``TestClient(api.app)`` starts it.

    The global application middleware retains this Settings object, so replacing
    only ``api.settings`` would leave auth/runtime startup consumers on native roots.
    """

    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    for name, value in (
        ("data_dir", data_dir),
        ("database_url", f"sqlite:///{data_dir / 'scanner.db'}"),
        ("exports_dir", data_dir / "exports"),
        ("qwen_replays_dir", data_dir / "qwen-replays"),
        ("log_dir", tmp_path / "logs"),
        ("cache_dir", cache_dir),
        ("model_cache", cache_dir / "models"),
        ("frames_dir", tmp_path / "frames"),
    ):
        monkeypatch.setattr(api.settings, name, value)


