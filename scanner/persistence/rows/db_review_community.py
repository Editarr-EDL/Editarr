"""Community filter snapshots stored in saved reviews (0039)."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from .db_core import Base


class ReviewCommunitySourceRow(Base):
    """A snapshot of one master link's active filters that a saved review includes.

    Reviews, exports and sidecars read only `filters_json`. `link_id` names the link
    the snapshot was taken from, for refresh and change detection, and is null once
    that link is deleted. `filters_digest` identifies the snapshot's filter values.
    """

    __tablename__ = "review_community_sources"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_review_community_sources_position"),
        CheckConstraint("master_revision >= 1", name="ck_review_community_sources_master_revision"),
        Index("ix_review_community_sources_link", "link_id"),
    )

    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), primary_key=True)
    id = Column(String(36), primary_key=True)
    link_id = Column(String(36), ForeignKey("community_master_links.id"), nullable=True)
    position = Column(Integer, nullable=False)
    community_version_id = Column(String(128), nullable=False)
    label = Column(String(256), nullable=False)
    tier = Column(String(8), nullable=True)
    match_percent = Column(Integer, nullable=True)
    master_revision = Column(Integer, nullable=False)
    alignment_json = Column(Text, nullable=True)
    filters_json = Column(Text, nullable=False)
    filters_digest = Column(String(64), nullable=False)
    captured_at = Column(DateTime, nullable=False)

    review = relationship("SavedReviewRow", back_populates="community_sources")
    link = relationship("CommunityMasterLinkRow")
