
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from scanner import repository
from scanner.db import (
    Base,
)
from tests.scan_access_support import owner_caller


class DummyDb:
    def close(self):
        return None


def _queue_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _enqueue_scan(db, suffix: str):
    media_item = repository.upsert_media_item(
        db,
        server_id="http://jellyfin.test",
        jellyfin_id=f"movie-{suffix}",
        library_id="movies",
        item_type="Movie",
        name=f"Example {suffix}",
        path=f"/media/example-{suffix}.mkv",
    )
    scan = repository.create_scan(
        db,
        media_item=media_item,
        resolved_local_path=f"/media/example-{suffix}.mkv",
        scan_audio=True,
        audio_model="whisper",
        audio_filter="better_profanity",
        scan_video=False,
        video_model=None,
        initiator=owner_caller(db).initiator,
    )
    return scan, repository.enqueue_scan(db, scan.id)


