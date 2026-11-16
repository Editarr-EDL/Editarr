"""The owner as a scan caller for router-level tests that call scan handlers directly.

Authorization itself is covered over real HTTP in `tests/test_scan_access_http.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import UserRow
from scanner.http.scan_access import ScanCaller
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.load import load_access
from tests.multi_user_access_support import add_linked_owner


def owner_caller(db: Session) -> ScanCaller:
    """The owner of `db` as a session caller, creating an owner account when none exists."""

    owner = db.scalar(select(UserRow.id).where(UserRow.role == "owner"))
    if owner is None:
        owner = add_linked_owner(db, "owner")
    access = load_access(db, UserId(owner), CredentialKind.SESSION)
    assert access is not None
    return ScanCaller(access, db, datetime.now(UTC))
