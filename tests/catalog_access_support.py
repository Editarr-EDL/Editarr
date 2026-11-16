"""MU-20 test double for calling catalog handlers directly as the owner.

Older unit tests call catalog handlers as plain functions with a database
session. The owner's records pass every media decision unchanged and keep
their paths, which is what `owner_viewer(db)` answers. Authorization itself is
tested through real HTTP in `tests/test_catalog_authorization_http.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from scanner.persistence.repository.review_access import ReviewAccess
from tests.scan_access_support import owner_caller


@dataclass
class _OwnerViewer:
    db: Session
    is_owner = True
    artwork_scope = "owner-test"

    @property
    def export_caller(self) -> ReviewAccess:
        return ReviewAccess(self.db, owner_caller(self.db).access)

    def sees_paths(self) -> bool:
        return True

    def library_visible(self, _library_id: str) -> bool:
        return True

    def visible(self, records, _target_of):
        return list(records)

    def require(self, _target, _capability=None) -> None:
        return None

    def resolve(self, path: str) -> str:
        return path


def owner_viewer(db: Session) -> _OwnerViewer:
    return _OwnerViewer(db)
