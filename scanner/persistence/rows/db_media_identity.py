"""Stable Editarr titles, file versions, and immutable provenance links."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, String, UniqueConstraint

from .db_core import Base


class MediaTitleRow(Base):
    __tablename__ = "media_titles"

    id = Column(String, primary_key=True)
    entity_type = Column(String, nullable=False)


class MediaTitleSourceRow(Base):
    """Keep each original Jellyfin binding even after its item disappears."""

    __tablename__ = "media_title_sources"

    media_item_id = Column(
        String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    title_id = Column(String, ForeignKey("media_titles.id"), nullable=False, index=True)


class MediaTitleAliasRow(Base):
    """One title claims each typed provider identifier."""

    __tablename__ = "media_title_aliases"

    provider = Column(String, primary_key=True)
    entity_type = Column(String, primary_key=True)
    value = Column(String, primary_key=True)
    title_id = Column(String, ForeignKey("media_titles.id"), nullable=False, index=True)


class MediaVersionRow(Base):
    """A verified byte sequence or a historical observation with weaker evidence."""

    __tablename__ = "media_versions"
    __table_args__ = (
        UniqueConstraint("title_id", "content_sha256", name="uq_media_versions_title_sha256"),
        CheckConstraint(
            "content_sha256 IS NULL OR (length(content_sha256) = 64 "
            "AND content_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_media_versions_sha256",
        ),
        Index("ix_media_versions_title_id", "title_id"),
    )

    id = Column(String, primary_key=True)
    title_id = Column(String, ForeignKey("media_titles.id"), nullable=False)
    content_sha256 = Column(String, nullable=True)
    video_hash = Column(String, nullable=True)


class MediaVersionLocatorRow(Base):
    """A source item, source ID, and path observed for a version."""

    __tablename__ = "media_version_locators"
    __table_args__ = (Index("ix_media_version_locators_media_item_id", "media_item_id"),)

    version_id = Column(String, ForeignKey("media_versions.id"), primary_key=True)
    media_item_id = Column(
        String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    media_source_id = Column(String, primary_key=True)
    source_path = Column(String, primary_key=True)
    resolved_path = Column(String, primary_key=True)


class ScanMediaVersionRow(Base):
    __tablename__ = "scan_media_versions"

    scan_id = Column(String, ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True)
    version_id = Column(String, ForeignKey("media_versions.id"), nullable=False, index=True)


class ReviewMediaVersionRow(Base):
    __tablename__ = "review_media_versions"

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    version_id = Column(String, ForeignKey("media_versions.id"), nullable=False, index=True)
