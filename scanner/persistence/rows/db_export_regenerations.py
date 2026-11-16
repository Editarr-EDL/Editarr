"""Durable regeneration work for one retained personal publication."""

from enum import StrEnum

from sqlalchemy import CheckConstraint, Column, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import relationship

from .db_core import Base


class RegenerationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WITHDRAWING = "withdrawing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WITHDRAWN = "withdrawn"


class RegenerationFailure(StrEnum):
    REGENERATION_FAILED = "regeneration_failed"
    RETRIES_EXHAUSTED = "retries_exhausted"
    DELETED = "deleted"


class ExportRegenerationRow(Base):
    """Policy-pinned work and outcome for one personal publication.

    Queued, running, and withdrawing work has a due time in Unix seconds; only that
    work appears in the due index. Withdrawing keeps the published bytes until their
    removal is durable, which `withdrawn` records. `failed` gave up without touching
    the file. `withdrawn` for `deleted` records an explicit DELETE made while the
    retained path was absent; it keeps the ownership evidence and is never regenerated.
    """

    __tablename__ = "media_item_export_regenerations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'withdrawing', 'succeeded', 'failed', 'withdrawn')",
            name="ck_export_regenerations_state",
        ),
        CheckConstraint(
            "(state IN ('queued', 'running', 'withdrawing')) = (due_at IS NOT NULL)",
            name="ck_export_regenerations_due",
        ),
        CheckConstraint(
            "(state IN ('withdrawing', 'failed', 'withdrawn')) = (failure_reason IS NOT NULL) "
            "AND (failure_reason IS NULL "
            "OR failure_reason IN ('regeneration_failed', 'retries_exhausted', 'deleted')) "
            "AND (failure_reason IS NOT 'deleted' OR state = 'withdrawn')",
            name="ck_export_regenerations_failure",
        ),
        CheckConstraint(
            "state != 'running' OR claim_token IS NOT NULL",
            name="ck_export_regenerations_claim",
        ),
        CheckConstraint(
            "attempts >= 0 AND policy_revision >= 0",
            name="ck_export_regenerations_counts",
        ),
        Index(
            "ix_export_regenerations_due",
            "due_at",
            "export_id",
            sqlite_where=text("due_at IS NOT NULL"),
        ),
    )

    export_id = Column(
        Integer,
        ForeignKey("media_item_exports.id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    state = Column(String, nullable=False)
    due_at = Column(Float, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(Float, nullable=True)
    policy_owner_id = Column(Integer, nullable=True)
    policy_revision = Column(Integer, nullable=False)
    claim_token = Column(String, nullable=True)
    failure_reason = Column(String, nullable=True)

    export = relationship("MediaItemExportRow", back_populates="regeneration")

    @property
    def deleted(self) -> bool:
        return self.failure_reason == RegenerationFailure.DELETED
