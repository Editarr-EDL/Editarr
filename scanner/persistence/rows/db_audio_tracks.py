"""Remembered local audio selection, scoped to a file and validated version."""

from sqlalchemy import Column, String, Text

from .db_core import Base


class AudioTrackSelectionRow(Base):
    __tablename__ = "audio_track_selections"

    file_path = Column(String, primary_key=True)
    selection_json = Column(Text, nullable=False)
