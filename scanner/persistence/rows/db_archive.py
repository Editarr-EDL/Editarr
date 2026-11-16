"""Read-only records kept from media sources Editarr no longer supports (0025 and 0026)."""

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Column, DateTime, Integer, String, Text

from .db_core import Base


class ArchivedSource(StrEnum):
    """A removed source. A media item with one is archived with all its scans,
    reviews, and exports; only the archive reads it."""

    RADARR = "radarr"
    SONARR = "sonarr"
    MANUAL_FILES = "manual_files"


class ArchivedConnectionRow(Base):
    """A removed source's connection, kept without its credentials."""

    __tablename__ = "arr_settings"

    service = Column(String, primary_key=True)
    server_url = Column(String, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class ArchivedMediaAccessRow(Base):
    """A media access grant for a removed source, kept as the JSON of its original row."""

    __tablename__ = "archived_media_access_rows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_table = Column(String, nullable=False)
    row_json = Column(Text, nullable=False)
