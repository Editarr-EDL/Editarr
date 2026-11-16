"""MU-18: Jellyfin sync after an ownership transfer between linked accounts.

The former owner is linked to a Jellyfin user who stops being a Jellyfin
administrator; the successor is linked to one Jellyfin disables. Sync leaves the
new owner active and the former owner an admin, as the transfer assigned.
"""

from scanner.persistence.rows.db_auth import UserProviderIdentityRow, UserRow
from scanner.persistence.write_lock import write_lock
from scanner.security.account_ownership import transfer_ownership
from tests.jellyfin_user_boundary import IDS, NOW
from tests.multi_user_access_support import OWNER
from tests.test_jellyfin_account_sync import _import, _linked, _row, _sync
from tests.test_jellyfin_account_sync import accounts as accounts
from tests.test_jellyfin_account_sync import boundary as boundary
from tests.test_jellyfin_account_sync import clock as clock


def test_sync_keeps_a_linked_successor_owner_and_its_former_owner_admin(accounts, boundary, clock):
    _import(accounts, boundary, clock, frozenset({IDS["Drew"]}))
    drew = _linked(accounts, "Drew")
    with accounts.factory() as db:
        db.add(
            UserProviderIdentityRow(
                user_id=OWNER,
                server_id=boundary.server_info()["Id"],
                provider_user_id=IDS["Avery"],
                link_state="verified",
                last_verified_at=NOW.replace(tzinfo=None),
            )
        )
        db.commit()
    with accounts.factory() as db, write_lock(db):
        assert transfer_ownership(db, OWNER, drew, now=NOW).value == "user"
        db.commit()

    boundary.state = "changed"
    _sync(accounts, boundary, clock)

    new_owner, former = _row(accounts, UserRow, drew), _row(accounts, UserRow, OWNER)
    assert (new_owner.role, new_owner.status) == ("owner", "active")
    assert (former.role, former.status) == ("admin", "active")
