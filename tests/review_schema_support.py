"""Fresh-schema and wire-contract checks; never use a runtime database."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from scanner.db import (
    Base,
    MediaItemRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.db'}")

    @event.listens_for(engine, "connect")
    def foreign_keys_on(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        media = MediaItemRow(
            id="media", server_id="test", jellyfin_id="item", item_type="Movie", name="Movie"
        )
        session.add(media)
        session.flush()
        for number in (1, 2):
            session.add(ScanRow(
                id=f"scan-{number}", media_item_id=media.id, scan_number=number,
                name="Duplicate", source_path="/test/movie.mkv",
                resolved_local_path="/test/movie.mkv",
            ))
        session.flush()
        session.add(ScanFindingRow(
            id=1, scan_id="scan-1", category=None, subcategory=None, start_seconds=1,
            end_seconds=2, confidence=1, source="audio", edit_action="mute", label="Evidence",
        ))
        session.add_all([
            SavedReviewRow(id="review-1", media_item_id=media.id, name=" Duplicate "),
            SavedReviewRow(id="review-2", media_item_id=media.id, name="Duplicate"),
        ])
        session.commit()
        yield session
    engine.dispose()


def snapshot(**overrides):
    return dict(
        review_id="review-1", id=str(uuid4()), source_scan_id="scan-1",
        source_finding_id=1, position=0, source="audio", category=None, subcategory=None,
        label="Private", details=None, group_key=None, start_seconds=1, end_seconds=2,
        confidence=1, edit_action="mute", selected=True,
    ) | overrides


def filter_input(**overrides):
    values = snapshot()
    for key in ("review_id", "position"):
        values.pop(key)
    return values | overrides


