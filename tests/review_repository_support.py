
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from scanner.db import (
    Base,
    MediaItemRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.models import (
    McfCategoryFamily,
)


def review_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    media = MediaItemRow(
        id="media",
        server_id="server",
        jellyfin_id="item",
        item_type="Movie",
        name="Movie",
    )
    scan = ScanRow(
        id="scan",
        media_item_id="media",
        scan_number=1,
        source_path="/tmp/movie.mkv",
        resolved_local_path="/tmp/movie.mkv",
        status="complete",
        duration_seconds=100,
        scan_audio=True,
        scan_video=False,
    )
    session.add_all(
        [
            media,
            scan,
            ScanFindingRow(
                scan_id="scan",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                start_seconds=1,
                end_seconds=2,
                confidence=0.9,
                label="Evidence",
                details="Source detail",
                source="audio",
                selected=True,
                edit_action="mute",
            ),
        ]
    )
    session.commit()
    return session


