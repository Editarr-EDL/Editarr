"""Review-owned recovery records for historical deleted scans."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from .db_core import Base


class ReviewScanRepairRow(Base):
    __tablename__ = "review_scan_repairs"
    __table_args__ = (
        UniqueConstraint("review_id", "missing_scan_id", name="uq_review_scan_repairs_scan"),
    )

    id = Column(Integer, primary_key=True)
    review_id = Column(String, ForeignKey("saved_reviews.id", ondelete="CASCADE"), nullable=False)
    missing_scan_id = Column(String, nullable=False)
    detected_at = Column(DateTime(timezone=True), nullable=False)
    preserved_json = Column(Text, nullable=False)
    community_json = Column(Text, nullable=True)
    community_unavailable_reason = Column(String, nullable=True)
