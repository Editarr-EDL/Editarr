"""Durable account-link transitions and the personal export files each one moves (0023)."""

from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
)
from sqlalchemy import text as sql
from sqlalchemy.orm import relationship

from .db_core import Base

_HEX = "NOT GLOB '*[^0-9a-f]*'"


def _export_id(column):
    return (
        f"(length({column}) = 38 AND substr({column}, 1, 6) = 'local-' "
        f"AND substr({column}, 7) {_HEX}) OR "
        f"(length({column}) = 32 AND {column} {_HEX} "
        f"AND {column} != '00000000000000000000000000000000')"
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LinkKind(StrEnum):
    LINK = "link"
    """A local account gains its first Jellyfin identity."""
    RELINK = "relink"
    """A linked account moves to the identity proven on a replacement server."""


class TransitionState(StrEnum):
    WRITING = "writing"
    """New files may exist; the identity has not changed. Recovery rolls back."""
    REMOVING = "removing"
    """The identity and publications changed; old owned files are still being removed."""
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


OPEN_STATES = (TransitionState.WRITING, TransitionState.REMOVING)


class RollbackReason(StrEnum):
    WRITE_FAILED = "write_failed"
    REFUSED = "refused"
    """The switch found the proof, account, or identity changed while files were written."""
    INTERRUPTED = "interrupted"


class MoveOutcome(StrEnum):
    """How a move's old path settled. Only `removed` is final: recovery keeps watching an
    `absent` or `kept` old path and removes the old bytes if they return there."""

    REMOVED = "removed"
    ABSENT = "absent"
    KEPT = "kept"
    """The old path held bytes Editarr did not write there; they were left untouched."""


class AccountLinkTransitionRow(Base):
    """One proven link or relink of an account and the state of its export moves.

    Only `writing` and `removing` are open, and an account has at most one open
    transition. Recovery decides from `state` alone: `writing` removes the new files
    it can recognize and rolls back; `removing` removes the old files it can recognize.
    """

    __tablename__ = "account_link_transitions"
    __table_args__ = (
        CheckConstraint("kind IN ('link', 'relink')", name="ck_account_link_transitions_kind"),
        CheckConstraint(
            "state IN ('writing', 'removing', 'completed', 'rolled_back')",
            name="ck_account_link_transitions_state",
        ),
        CheckConstraint(
            "(kind = 'link') = (from_server_id IS NULL) "
            "AND (from_server_id IS NULL) = (from_provider_user_id IS NULL)",
            name="ck_account_link_transitions_from",
        ),
        CheckConstraint(
            f"length(to_server_id) > 0 AND length(to_provider_user_id) = 32 "
            f"AND to_provider_user_id {_HEX} "
            f"AND to_provider_user_id != '00000000000000000000000000000000'",
            name="ck_account_link_transitions_to",
        ),
        CheckConstraint(
            f"({_export_id('from_export_id')}) "
            "AND (kind = 'link') = (substr(from_export_id, 1, 6) = 'local-') "
            "AND (kind = 'link' OR from_export_id = from_provider_user_id)",
            name="ck_account_link_transitions_from_export_id",
        ),
        CheckConstraint(
            "(state = 'rolled_back') = (failure_reason IS NOT NULL) "
            "AND (failure_reason IS NULL "
            "OR failure_reason IN ('write_failed', 'refused', 'interrupted'))",
            name="ck_account_link_transitions_failure",
        ),
        Index(
            "uq_account_link_transitions_open",
            "user_id",
            unique=True,
            sqlite_where=sql("state IN ('writing', 'removing')"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kind = Column(String, nullable=False)
    state = Column(String, nullable=False)
    from_server_id = Column(String, nullable=True)
    from_provider_user_id = Column(String, nullable=True)
    to_server_id = Column(String, nullable=False)
    to_provider_user_id = Column(String, nullable=False)
    from_export_id = Column(String, nullable=False)
    failure_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, nullable=False)

    moves = relationship(
        "AccountLinkExportMoveRow",
        back_populates="transition",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AccountLinkExportMoveRow.id",
    )


class AccountLinkExportMoveRow(Base):
    """One personal publication's file move: old and new paths with their content digests.

    `new_sha256` is null for a publication without served content, which moves its
    record only. `old_sha256` is the ownership evidence for the old path. `new_file_id`
    is the inode number of the file Editarr created at the staging name, committed
    before placement: with `new_sha256` it is the ownership evidence for the new path,
    and while it is null nothing was placed there. It omits the device number, which a
    remount can renumber. `requeued`
    records that the owned bytes now at the old path already re-queued a withdrawn
    publication, so bytes that cannot be removed re-queue it once, not on every pass.
    """

    __tablename__ = "account_link_export_moves"
    __table_args__ = (
        CheckConstraint("old_path != new_path", name="ck_account_link_export_moves_paths"),
        CheckConstraint(
            "(old_sha256 IS NULL OR (length(old_sha256) = 64 AND old_sha256 "
            f"{_HEX})) AND (new_sha256 IS NULL OR (length(new_sha256) = 64 "
            f"AND new_sha256 {_HEX}))",
            name="ck_account_link_export_moves_digests",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('removed', 'absent', 'kept')",
            name="ck_account_link_export_moves_outcome",
        ),
        CheckConstraint("requeued IN (0, 1)", name="ck_account_link_export_moves_requeued"),
        CheckConstraint(
            "new_file_id IS NULL OR new_sha256 IS NOT NULL",
            name="ck_account_link_export_moves_new_file",
        ),
        Index("ix_account_link_export_moves_transition", "transition_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    transition_id = Column(
        Integer, ForeignKey("account_link_transitions.id", ondelete="CASCADE"), nullable=False
    )
    export_id = Column(
        Integer, ForeignKey("media_item_exports.id", ondelete="SET NULL"), nullable=True
    )
    old_path = Column(Text, nullable=False)
    new_path = Column(Text, nullable=False)
    old_sha256 = Column(String, nullable=True)
    new_sha256 = Column(String, nullable=True)
    new_file_id = Column(String, nullable=True)
    outcome = Column(String, nullable=True)
    requeued = Column(Boolean, nullable=False, server_default=false())

    transition = relationship("AccountLinkTransitionRow", back_populates="moves")
