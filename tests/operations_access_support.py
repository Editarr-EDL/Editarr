"""Owner access for router-level tests of settings and operations routes (MU-17).

`as_owner` gives a bare FastAPI app the principal the access middleware would
record for the owner's browser session, backed by `live_accounts` from
`tests.multi_user_access_support` (`mu04-access-builders-3`). Audit events
land in that database. Authorization itself is covered over real HTTP in
`tests/test_operations_access_http.py`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.security.access.identity import CredentialKind
from scanner.security.access.load import load_access
from scanner.security.access.request_access import install_access_errors, request_access
from tests.multi_user_access_support import OWNER, OWNER_PASSWORD, LiveAccounts, live_accounts

FRESH = {"reauthentication": {"password": OWNER_PASSWORD}}


def as_owner(application: FastAPI, directory: Path) -> LiveAccounts:
    """Serve `application` as the owner's session; the caller closes the returned accounts."""

    directory.mkdir(parents=True, exist_ok=True)
    accounts = live_accounts(directory)
    session_id = accounts.sessions[OWNER].id

    def database():
        with accounts.factory() as session:
            yield session

    def owner(db: Session = Depends(get_db)):
        return load_access(db, OWNER, CredentialKind.SESSION, session_id)

    application.dependency_overrides[get_db] = database
    application.dependency_overrides[request_access] = owner
    install_access_errors(application)
    return accounts
