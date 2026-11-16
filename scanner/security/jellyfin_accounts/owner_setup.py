"""First-run setup: make a Jellyfin administrator the owner and connect Editarr to that server.

The steps run in one order and every exit leaves either nothing or everything:

1. Sign the administrator in. A refusal stores nothing.
2. Take SQLite's write lock and check again that no owner exists. A setup that
   loses a race refuses here, before it mints anything.
3. Mint the `Editarr` key while holding the lock.
4. Stage the connection, the owner linked to the administrator's Jellyfin
   identity, its audit event, and its session, then commit. If anything fails
   after the key exists, the key is revoked; a key Jellyfin will not delete
   refuses with `KEY_LEFT_BEHIND` so the operator can remove it.

The administrator's Jellyfin session ends whatever happens.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from sqlalchemy.orm import Session

from scanner.integrations.jellyfin_setup import (
    JellyfinSetupClient,
    SetupRefusal,
    SetupRefused,
)
from scanner.modeling.model_catalog import JellyfinSettingsUpdate
from scanner.persistence.repository.repository_settings import update_jellyfin_settings
from scanner.persistence.write_lock import write_lock
from scanner.security.access.vocabulary import Role
from scanner.security.audit import authentication as stored_audit
from scanner.security.auth_repository_admin import adopt_unowned, owner_id

from . import reads, store
from .admission import create_linked, encodable

T = TypeVar("T")
logger = logging.getLogger(__name__)


def set_up_owner(
    db: Session,
    client: JellyfinSetupClient,
    username: str,
    password: str,
    *,
    clock: Callable[[], datetime],
    issue: Callable[[int], T],
) -> T:
    """Create the owner from a Jellyfin administrator and store the minted key.

    `issue` stages the first session for the new owner inside the setup
    transaction and its result is returned. Raises `SetupRefused` otherwise.
    """

    if not encodable(username, password):
        raise SetupRefused(SetupRefusal.INVALID_CREDENTIALS)
    if owner_id(db) is not None:
        raise SetupRefused(SetupRefusal.ALREADY_COMPLETE)
    read = reads.begin_read(db, clock)
    administrator = client.sign_in_administrator(username, password)
    try:
        with write_lock(db):
            if owner_id(db) is not None:
                raise SetupRefused(SetupRefusal.ALREADY_COMPLETE)
            key = client.mint_key(administrator)
            try:
                update_jellyfin_settings(
                    db,
                    JellyfinSettingsUpdate(server_url=administrator.server_url, api_key=key),
                    commit=False,
                )
                user_id = create_linked(db, administrator.user, read.at, Role.OWNER)
                store.observe(db, user_id, administrator.user, read)
                adopt_unowned(db, user_id)
                stored_audit.setup_completed(db, user_id, now=read.at)
                issued = issue(user_id)
                db.commit()
            except BaseException as error:
                db.rollback()
                try:
                    client.revoke_keys(administrator, (key,))
                except SetupRefused:
                    logger.error("Setup failed after minting its Jellyfin key", exc_info=error)
                    raise
                raise
    finally:
        client.end_session(administrator)
    return issued
