"""Deleted accounts and the personal export files a transfer moves (0024)."""

from datetime import datetime, timezone

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

from .db_core import Base

_HEX = "NOT GLOB '*[^0-9a-f]*'"


class AccountDispositionRow(Base):
    """One row per deleted account; its presence makes the account a tombstone.

    It commits in the same transaction as every change it describes, so the
    impact the actor confirmed and the per-resource result are never lost. It
    belongs to the account backup family and travels with `users`.
    """

    __tablename__ = "account_dispositions"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('retain', 'transfer', 'delete')", name="ck_account_dispositions_mode"
        ),
        CheckConstraint(
            "(mode = 'transfer') = (destination_id IS NOT NULL) "
            "AND (destination_id IS NULL OR destination_id != user_id)",
            name="ck_account_dispositions_destination",
        ),
        CheckConstraint(
            "length(confirmation) = 64 AND confirmation NOT GLOB '*[^0-9a-f]*'",
            name="ck_account_dispositions_confirmation",
        ),
    )

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    mode = Column(String, nullable=False)
    destination_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmation = Column(String, nullable=False)
    impact_json = Column(Text, nullable=False)
    result_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class AccountTransferMoveRow(Base):
    """One personal publication a transfer moves to the destination's export ID.

    The columns carry MU-35's ownership evidence: `old_sha256` for the old path,
    and `new_file_id` with `new_sha256` for the file Editarr placed at the new path,
    committed before placement. `confirmation` binds the row to the transfer the
    actor confirmed: the transfer switched only when the account's disposition is a
    transfer with that confirmation, and recovery then removes the old files.
    Otherwise it never switched, so recovery discards the new files and the rows.
    A settled `absent` or `kept` old path stays watched for returning owned bytes.
    """

    __tablename__ = "account_transfer_moves"
    __table_args__ = (
        CheckConstraint("old_path != new_path", name="ck_account_transfer_moves_paths"),
        CheckConstraint(
            f"(old_sha256 IS NULL OR (length(old_sha256) = 64 AND old_sha256 {_HEX})) "
            f"AND (new_sha256 IS NULL OR (length(new_sha256) = 64 AND new_sha256 {_HEX}))",
            name="ck_account_transfer_moves_digests",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('removed', 'absent', 'kept')",
            name="ck_account_transfer_moves_outcome",
        ),
        CheckConstraint("requeued IN (0, 1)", name="ck_account_transfer_moves_requeued"),
        CheckConstraint(
            f"length(confirmation) = 64 AND confirmation {_HEX}",
            name="ck_account_transfer_moves_confirmation",
        ),
        CheckConstraint(
            "new_file_id IS NULL OR new_sha256 IS NOT NULL",
            name="ck_account_transfer_moves_new_file",
        ),
        Index("ix_account_transfer_moves_user", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    confirmation = Column(String, nullable=False)
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
