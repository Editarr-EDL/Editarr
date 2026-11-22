# ruff: noqa: F811
"""A linked account's username is its Jellyfin user's Name, refreshed at sign-in and sync.

When another account already holds that name the account keeps its username,
an `account.jellyfin.state` event says so, and the next sign-in or sync tries
again. Harness: `tests/test_jellyfin_accounts_http.py`.
"""

from __future__ import annotations

import json

from sqlalchemy import update

from scanner.db import UserRow
from scanner.persistence.migrations import adoption
from tests.community_schema_fixture import SYNTHETIC_PASSWORD_HASH
from tests.jellyfin_user_boundary import IDS
from tests.multi_user_access_support import ORIGIN, OWNER, PEER_USER
from tests.owner_schema_support import (
    undo_explicit_filter_modes_migration,
    undo_manual_filter_tracking_migration,
    undo_remove_passwords_migration,
    undo_scan_read_indexes_migration,
    undo_stable_media_identity_migration,
)
from tests.test_jellyfin_accounts_http import (
    PASSWORDS,
    _linked,
    _sync,
    app,  # noqa: F401
    boundary,  # noqa: F401
    clock,  # noqa: F401
)
from tests.users_api_http_support import fresh


def _usernames(app) -> dict[int, str | None]:
    listed = app.ok("GET", "/api/users?limit=100", OWNER)["items"]
    return {account["id"]: account["username"] for account in listed}


def _profile_username(app, user_id: int) -> str | None:
    return app.ok("GET", f"/api/users/{user_id}", OWNER)["username"]


def _username_events(app) -> list[tuple[int, str]]:
    return [
        (target, json.loads(details)["username"])
        for action, _outcome, _actor, target, details in app.audit()
        if action == "account.jellyfin.state" and "username" in json.loads(details)
    ]


def _sign_in_as(app, name: str, jellyfin_id: str):
    app.client.cookies.clear()
    body = {"username": name, "password": PASSWORDS[jellyfin_id], "remember_me": False}
    return app.client.post("/api/auth/jellyfin/login", json=body, headers=ORIGIN)


def test_import_and_first_sign_in_take_the_jellyfin_name_as_the_username(app):
    app.ok("POST", "/api/jellyfin/users/import", OWNER, {"user_ids": [IDS["Avery"]]})
    assert _sign_in_as(app, "Blake", IDS["Blake"]).status_code == 200

    avery, blake = _linked(app, "Avery"), _linked(app, "Blake")
    assert {avery: "Avery", blake: "Blake"}.items() <= _usernames(app).items()
    assert (_profile_username(app, avery), _profile_username(app, blake)) == ("Avery", "Blake")


def test_a_sync_renames_and_a_held_name_waits_for_the_next_sync(app, boundary):
    app.ok("POST", "/api/jellyfin/users/import", OWNER, {"all": True})
    blake, casey = _linked(app, "Blake"), _linked(app, "Casey")
    boundary.state = "changed"

    _sync(app)

    assert _usernames(app)[casey] == "Casey Archive"
    assert _usernames(app)[blake] == "Blake"
    assert sorted(_username_events(app)) == [(blake, "unavailable"), (casey, "refreshed")]

    _sync(app)

    assert _usernames(app)[blake] == "Casey"
    assert _profile_username(app, blake) == "Casey"
    assert _username_events(app)[-1] == (blake, "refreshed")


def test_a_sign_in_renames_the_account(app, boundary):
    app.ok("POST", "/api/jellyfin/users/import", OWNER, {"user_ids": [IDS["Casey"]]})
    casey = _linked(app, "Casey")
    boundary.state = "changed"

    assert _sign_in_as(app, "Casey Archive", IDS["Casey"]).status_code == 200

    assert _profile_username(app, casey) == "Casey Archive"
    assert _username_events(app) == [(casey, "refreshed")]


def test_an_account_holding_the_name_keeps_it_until_its_deletion_releases_it(app):
    with app.accounts.factory() as db:
        db.execute(
            update(UserRow)
            .where(UserRow.id == PEER_USER)
            .values(username="casey", normalized_username="casey")
        )
        db.commit()

    assert _sign_in_as(app, "Casey", IDS["Casey"]).status_code == 200
    casey = _linked(app, "Casey")
    assert _usernames(app)[casey] is None
    assert _usernames(app)[PEER_USER] == "casey"
    assert _username_events(app) == [(casey, "unavailable")]

    _sync(app)
    assert _username_events(app) == [(casey, "unavailable")]

    preview = app.ok("GET", f"/api/users/{PEER_USER}/deletion?mode=retain", OWNER)
    body = {"mode": "retain", "confirmation": preview["confirmation"], **fresh(OWNER)}
    assert app.send("POST", f"/api/users/{PEER_USER}/deletion", OWNER, body).status_code == 200
    _sync(app)
    assert _usernames(app)[casey] == "Casey"


def test_a_name_a_password_account_held_before_0030_comes_with_the_next_sync(app):
    """0030 clears unlinked usernames, so a Jellyfin account held by one gets its name."""
    with app.accounts.factory() as db:
        db.execute(
            update(UserRow)
            .where(UserRow.id == PEER_USER)
            .values(username="casey", normalized_username="casey")
        )
        db.commit()
    assert _sign_in_as(app, "Casey", IDS["Casey"]).status_code == 200
    casey = _linked(app, "Casey")
    assert _usernames(app)[casey] is None
    with app.accounts.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = OFF")
        undo_stable_media_identity_migration(connection)
        undo_scan_read_indexes_migration(connection)
        undo_manual_filter_tracking_migration(connection)
        undo_explicit_filter_modes_migration(connection)
        undo_remove_passwords_migration(connection)
        connection.exec_driver_sql(
            "UPDATE users SET password_hash = ?, local_credential = 'set', "
            "password_changed_at = updated_at WHERE id = ?",
            (SYNTHETIC_PASSWORD_HASH, PEER_USER),
        )
        connection.exec_driver_sql(
            "UPDATE alembic_version SET version_num = '0029_jellyfin_user_blocks'"
        )
    app.accounts.engine.dispose()

    adoption.upgrade_existing_database(app.accounts.path, app.accounts.path.parent)
    _sync(app)

    listed = app.send("GET", "/api/users?limit=100", OWNER, key=True).json()["items"]
    usernames = {account["id"]: account["username"] for account in listed}
    assert (usernames[casey], usernames[PEER_USER]) == ("Casey", None)
    assert _username_events(app) == [(casey, "unavailable"), (casey, "refreshed")]
