"""Number Jellyfin reads in the order they begin, from the database, never from the wall clock.

Every read whose answer can change an account (a sign-in, an import listing,
one account's sync verification) first takes the next generation from
`account_settings.jellyfin_read_generation` in its own short write
transaction. `store.observe` applies an answer only over a lower generation, so
an older read that answers late never wins, however the wall clock moved. The
counter lives in the database: it orders reads across threads, processes, and
restarts, and a backup restore moves it together with the accounts it orders.

The wall-clock time each read takes is kept beside the counter. A read that
finds the clock behind the previous read's time knows the clock stepped back,
and verification ages measured on the old clock can no longer be trusted, so it
clears every stored verification time: last-verified access then lasts only
until each account verifies again, never longer. In the same transaction it
records its generation as `jellyfin_clock_fence`, and `store.observe` never
takes a verification from a read numbered below the fence: such a read was
timed on the old clock, and if it answered late it would restore what the
clear removed.

A restore replaces the counter, the fence, and the accounts with older ones,
while a read begun before it may still be waiting for Jellyfin. Each read
therefore also takes `jellyfin_read_epoch`, a random value the first read in a
database sets and every restore activation clears (`backup.activation_state`).
`store.observe` applies nothing from a read of another epoch: its generation
and account IDs were numbered in a database that no longer exists.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NewType

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from scanner.persistence.rows.db_auth import AccountSettingsRow, UserProviderIdentityRow
from scanner.persistence.write_lock import write_lock

logger = logging.getLogger(__name__)

ReadGeneration = NewType("ReadGeneration", int)
"""A Jellyfin read's place in the database-wide read order; higher began later."""

ReadEpoch = NewType("ReadEpoch", str)
"""The database incarnation a read was numbered in: 32 random hex digits."""


class ReadCancelled(Exception):
    """The run was cancelled before this read took a generation."""


@dataclass(frozen=True)
class ReadStart:
    """When one Jellyfin read began: its epoch and generation, which order it, and the wall time.

    Only `epoch` and `generation` order observations. `at` times the attempt and,
    for a verified answer, the verification that grace is measured from.
    """

    generation: ReadGeneration
    at: datetime
    epoch: ReadEpoch


def _stored(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def begin_read(
    db: Session,
    clock: Callable[[], datetime],
    *,
    cancelled: threading.Event | None = None,
) -> ReadStart:
    """Take the next generation, the epoch, and the time, and commit them, before Jellyfin is asked.

    Commits the session, so call it before staging anything else. `clock` is
    read under the write lock, so generations and times taken on one clock
    never disagree unless the clock itself stepped back. Raises `ReadCancelled`,
    writing nothing, when `cancelled` is set.
    """

    with write_lock(db):
        if cancelled is not None and cancelled.is_set():
            db.rollback()
            raise ReadCancelled
        now = clock()
        previous = db.execute(
            select(
                AccountSettingsRow.jellyfin_read_generation,
                AccountSettingsRow.jellyfin_read_at,
                AccountSettingsRow.jellyfin_read_epoch,
            )
        ).one_or_none()
        last_at = previous.jellyfin_read_at if previous is not None else None
        generation = ReadGeneration((previous.jellyfin_read_generation if previous else 0) + 1)
        epoch = ReadEpoch(
            previous.jellyfin_read_epoch
            if previous is not None and previous.jellyfin_read_epoch is not None
            else secrets.token_hex(16)
        )
        values = {
            "jellyfin_read_generation": generation,
            "jellyfin_read_at": _stored(now),
            "jellyfin_read_epoch": epoch,
        }
        if last_at is not None and _stored(now) < last_at:
            logger.warning(
                "The clock is behind the previous Jellyfin read; "
                "every linked account must verify again before last-verified access resumes"
            )
            db.execute(update(UserProviderIdentityRow).values(last_verified_at=None))
            values["jellyfin_clock_fence"] = generation
        db.execute(
            sqlite_insert(AccountSettingsRow)
            .values(id=1, updated_at=_stored(now), **values)
            .on_conflict_do_update(index_elements=[AccountSettingsRow.id], set_=values)
        )
        db.commit()
    return ReadStart(generation, now, epoch)


def read_epoch(db: Session) -> ReadEpoch | None:
    """The epoch reads take now; None before the first read since the last restore.

    Read it under the write lock that stores the answer it judges.
    """

    return db.scalar(select(AccountSettingsRow.jellyfin_read_epoch))


def clock_fence(db: Session) -> ReadGeneration:
    """The generation of the read that last found the clock stepped back; 0 before any.

    Read it under the write lock that stores the answer it judges.
    """

    return ReadGeneration(db.scalar(select(AccountSettingsRow.jellyfin_clock_fence)) or 0)
