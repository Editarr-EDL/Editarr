"""Stable Editarr titles, file versions, and immutable provenance links."""

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, UniqueConstraint

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
    """A sampled file identity, a retained whole-file identity, or historical evidence."""

    __tablename__ = "media_versions"
    __table_args__ = (
        UniqueConstraint("title_id", "content_sha256", name="uq_media_versions_title_sha256"),
        CheckConstraint(
            "content_sha256 IS NULL OR (length(content_sha256) = 64 "
            "AND content_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_media_versions_sha256",
        ),
        Index("ix_media_versions_title_id", "title_id"),
        Index("ix_media_versions_title_sample", "title_id", "sampled_md5", "sample_size_bytes"),
    )

    id = Column(String, primary_key=True)
    title_id = Column(String, ForeignKey("media_titles.id"), nullable=False)
    content_sha256 = Column(String, nullable=True)
    video_hash = Column(String, nullable=True)
    sampled_md5 = Column(
        String,
        CheckConstraint(
            "sampled_md5 IS NULL OR (length(sampled_md5) = 32 "
            "AND sampled_md5 NOT GLOB '*[^0-9a-f]*')",
            name="ck_media_versions_sampled_md5",
        ),
        nullable=True,
    )
    sample_size_bytes = Column(
        Integer,
        CheckConstraint(
            "(sample_size_bytes IS NULL AND sampled_md5 IS NULL) OR "
            "(sample_size_bytes IS NOT NULL AND sample_size_bytes >= 0 "
            "AND sampled_md5 IS NOT NULL)",
            name="ck_media_versions_sample_size",
        ),
        nullable=True,
    )


class MediaVersionLocatorRow(Base):
    """A source item, source ID, and path observed for a version."""

    __tablename__ = "media_version_locators"
    __table_args__ = (
        Index("ix_media_version_locators_media_item_id", "media_item_id"),
        Index("ix_media_version_locators_sample_generation", "resolved_path", "sample_generation"),
    )

    version_id = Column(String, ForeignKey("media_versions.id"), primary_key=True)
    media_item_id = Column(
        String, ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    media_source_id = Column(String, primary_key=True)
    source_path = Column(String, primary_key=True)
    resolved_path = Column(String, primary_key=True)
    sample_generation = Column(String, nullable=True)


class ScanMediaVersionRow(Base):
    __tablename__ = "scan_media_versions"

    scan_id = Column(String, ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True)
    version_id = Column(String, ForeignKey("media_versions.id"), nullable=False, index=True)


class ReviewMediaVersionRow(Base):
    __tablename__ = "review_media_versions"

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    version_id = Column(String, ForeignKey("media_versions.id"), nullable=False, index=True)
