"""Stored audit events. MU-10 writes them and expires them after `AUDIT_RETENTION`."""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text

from .db_core import Base


class AuditEventRow(Base):
    """One secret-free `AuditEvent` with the account and media scope that admin queries use.

    `actor_kind` says who acted. `user` names an account in `actor_id`.
    `unattributed` marks an event recorded before per-account attribution, which
    keeps its historical labels in `details_json` and never gains an account.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("length(event_id) = 36", name="ck_audit_events_event_id"),
        CheckConstraint("length(action) BETWEEN 1 AND 64", name="ck_audit_events_action"),
        CheckConstraint(
            "outcome IN ('succeeded', 'denied', 'failed')", name="ck_audit_events_outcome"
        ),
        CheckConstraint(
            "actor_kind IN ('user', 'system', 'anonymous', 'unattributed')",
            name="ck_audit_events_actor_kind",
        ),
        CheckConstraint(
            "(actor_kind = 'user') = (actor_id IS NOT NULL)", name="ck_audit_events_actor"
        ),
        CheckConstraint(
            "credential IS NULL OR (actor_kind = 'user' AND credential IN ('session', 'api_key'))",
            name="ck_audit_events_credential",
        ),
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_actor_id", "actor_id"),
        Index("ix_audit_events_target_user_id", "target_user_id"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=False, unique=True)
    occurred_at = Column(DateTime, nullable=False)
    action = Column(String(64), nullable=False)
    outcome = Column(String(9), nullable=False)
    actor_kind = Column(String(12), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    credential = Column(String(7), nullable=True)
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    media_key = Column(String, nullable=True)
    resource = Column(String, nullable=True)
    request_id = Column(String(36), nullable=True)
    details_json = Column(Text, nullable=False, default="{}")
