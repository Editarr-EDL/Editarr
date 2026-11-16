"""MU-04: live permissions through the real access middleware and TEST FIXTURE routes.

The fixture routes resolve explicit targets and call the shared authorization
interface, the same way every gated production handler does.
"""

import itertools
import sqlite3
from contextlib import closing, contextmanager

import pytest
from sqlalchemy import event, update
from starlette.requests import Request

from scanner.db import UserRow
from scanner.security.access.api_shapes import AccessErrorCode as E
from scanner.security.access.api_shapes import Reauthentication
from scanner.security.access.authorization import authorize_job
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import Allowed, Denied
from scanner.security.access.evaluator import decide
from scanner.security.access.fresh import verify_reauthentication
from scanner.security.access.identity import AccountStatus, CredentialKind, UserId
from scanner.security.access.load import load_access
from scanner.security.access.overrides import OverrideEffect as O
from scanner.security.access.request_access import (
    AuthenticatedCredential,
    record_credential,
    request_access,
)
from scanner.security.access.targets import INSTANCE, AccountRef
from scanner.security.access.vocabulary import Role
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import (
    ADMIN,
    MEMBER_PASSWORD,
    OWNER,
    OWNER_PASSWORD,
    PEER_ADMIN,
    PEER_USER,
    USER,
    live_accounts,
)


@pytest.fixture
def accounts(tmp_path):
    built = live_accounts(tmp_path)
    yield built
    built.close()


@pytest.fixture
def serve(monkeypatch, accounts):
    clients = []

    def start():
        client = serve_access_fixture(monkeypatch, accounts)
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.close()


def _code(response):
    return response.status_code, response.json().get("code")


def _instance(client, headers, capability):
    return client.get(f"/api/test-access/instance/{capability.value}", headers=headers)


def test_every_admitted_account_reaches_handlers_that_decide_for_themselves(accounts, serve):
    client = serve()
    for user_id in (OWNER, ADMIN, USER):
        for credential in (accounts.cookie(user_id), accounts.key(user_id)):
            assert client.get("/api/protected", headers=credential).status_code == 200
            assert _instance(client, credential, C.LOGS_VIEW).status_code == (
                403 if user_id == USER else 200
            )


def test_role_defaults_and_overrides_reach_existing_cookies_and_keys(accounts, serve):
    client = serve()
    cookie, key, peer = accounts.cookie(USER), accounts.key(USER), accounts.key(PEER_USER)

    def outcomes():
        return [
            _instance(client, credential, C.MODELS_DOWNLOAD).status_code
            for credential in (cookie, key, peer)
        ]

    def payload(credential):
        return client.get("/api/test-access/payload", headers=credential).json()

    before = payload(cookie)
    assert outcomes() == [200, 200, 200]

    accounts.set_role_default(Role.USER, C.MODELS_DOWNLOAD, False)
    assert outcomes() == [403, 403, 403]
    assert _code(_instance(client, key, C.MODELS_DOWNLOAD)) == (403, "capability_denied")
    after = payload(cookie)
    assert after["permission_revision"] == before["permission_revision"] + 1
    assert "models.download" in before["capabilities"]
    assert "models.download" not in after["capabilities"]
    assert _instance(client, accounts.cookie(ADMIN), C.MODELS_DOWNLOAD).status_code == 200

    accounts.set_override(USER, C.MODELS_DOWNLOAD, O.ALLOW)
    assert outcomes() == [200, 200, 403]
    accounts.set_role_default(Role.USER, C.MODELS_DOWNLOAD, None)
    assert outcomes() == [200, 200, 200]
    accounts.set_override(USER, C.MODELS_DOWNLOAD, O.DENY)
    assert outcomes() == [403, 403, 200]
    accounts.set_override(USER, C.MODELS_DOWNLOAD, O.INHERIT)
    assert outcomes() == [200, 200, 200]

    assert _instance(client, cookie, C.MODELS_DELETE).status_code == 403
    accounts.set_role_default(Role.USER, C.MODELS_DELETE, True)
    assert _instance(client, cookie, C.MODELS_DELETE).status_code == 200
    assert "models.delete" in payload(key)["capabilities"]


def test_status_and_role_changes_reach_existing_cookies_and_keys(accounts, serve):
    client = serve()
    cookie, key = accounts.cookie(USER), accounts.key(USER)
    accounts.set_status(USER, AccountStatus.DISABLED)
    for credential in (cookie, key):
        assert _code(_instance(client, credential, C.MODELS_DOWNLOAD)) == (403, "account_disabled")
    accounts.set_status(USER, AccountStatus.ACTIVE)
    assert _instance(client, cookie, C.MODELS_DOWNLOAD).status_code == 200

    assert _instance(client, key, C.ACCOUNTS_LIST).status_code == 403
    accounts.set_role(USER, Role.ADMIN)
    assert _instance(client, key, C.ACCOUNTS_LIST).status_code == 200
    accounts.set_role(USER, Role.USER)
    assert _instance(client, cookie, C.ACCOUNTS_LIST).status_code == 403

    with accounts.factory() as worker:
        queued = authorize_job(worker, USER, CredentialKind.SESSION, C.MODELS_DOWNLOAD)
    assert queued == Allowed(C.MODELS_DOWNLOAD)


def _demote_admin_and_grant_admins_logs(accounts):
    """One concurrent commit: both its before and after states deny the admin `logs.view`."""

    with closing(sqlite3.connect(accounts.path, timeout=2)) as writer, writer:
        writer.execute("UPDATE users SET role = 'user' WHERE id = ?", (ADMIN,))
        writer.execute(
            "UPDATE role_capability_defaults SET granted = 1 "
            "WHERE role = 'admin' AND capability = ?",
            (C.LOGS_VIEW.value,),
        )
        writer.execute(
            "UPDATE users SET permission_revision = permission_revision + 1 "
            "WHERE id = ? OR role = 'admin'",
            (ADMIN,),
        )


@contextmanager
def _commit_before_statement(accounts, number):
    """Run the concurrent commit just before the `number`th statement outside a transaction.

    Yields a list that is non-empty once the commit ran. Statements inside a
    write transaction are skipped, since no other writer could commit there.
    """

    seen, fired = 0, []

    def before(connection, *_):
        nonlocal seen
        if connection.connection.driver_connection.in_transaction:
            return
        seen += 1
        if seen == number:
            _demote_admin_and_grant_admins_logs(accounts)
            fired.append(number)

    event.listen(accounts.engine, "before_cursor_execute", before)
    try:
        yield fired
    finally:
        event.remove(accounts.engine, "before_cursor_execute", before)


def _interleavings(accounts):
    """Each point at which the concurrent commit can land, from the same starting state."""

    for number in itertools.count(1):
        accounts.set_role(ADMIN, Role.ADMIN)
        accounts.set_role_default(Role.ADMIN, C.LOGS_VIEW, False)
        with _commit_before_statement(accounts, number) as fired:
            yield fired
        if not fired:
            return


def test_a_concurrent_commit_never_mixes_two_states_into_one_principal(accounts):
    def state(access):
        return access.principal.account.role, access.capabilities

    accounts.set_role_default(Role.ADMIN, C.LOGS_VIEW, False)
    before = state(accounts.access(ADMIN, CredentialKind.API_KEY))
    _demote_admin_and_grant_admins_logs(accounts)
    after = state(accounts.access(ADMIN, CredentialKind.API_KEY))
    assert before[0] is Role.ADMIN and after[0] is Role.USER
    assert C.LOGS_VIEW not in before[1] | after[1]

    loaded = []
    for fired in _interleavings(accounts):
        access = accounts.access(ADMIN, CredentialKind.API_KEY)
        if fired:
            loaded.append(state(access))
    assert loaded
    assert all(found in (before, after) for found in loaded), loaded


def test_a_concurrent_commit_during_a_request_never_grants_what_both_states_deny(accounts, serve):
    client = serve()
    outcomes = []
    for fired in _interleavings(accounts):
        response = _instance(client, accounts.key(ADMIN), C.LOGS_VIEW)
        if fired:
            outcomes.append(_code(response))
    assert len(outcomes) >= 2
    assert set(outcomes) == {(403, "capability_denied")}, outcomes


def test_loading_reads_inside_the_callers_transaction_without_ending_it(accounts):
    with accounts.factory() as db:
        db.execute(update(UserRow).where(UserRow.id == USER).values(role=Role.ADMIN.value))
        assert accounts.access(USER).principal.account.role is Role.USER
        assert load_access(db, USER, CredentialKind.API_KEY).principal.account.role is Role.ADMIN
        assert db.connection().connection.driver_connection.in_transaction
        db.rollback()
    assert accounts.access(USER).principal.account.role is Role.USER


def test_a_proof_verified_in_one_request_is_refused_in_the_next(accounts):
    credential = AuthenticatedCredential(USER, CredentialKind.SESSION, accounts.sessions[USER].id)

    def new_request():
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
        record_credential(request, credential)
        with accounts.factory() as db:
            return request_access(request, db)

    first, second = new_request(), new_request()
    assert first == second
    with accounts.factory() as db:
        proof = verify_reauthentication(
            db, first, Reauthentication(password=MEMBER_PASSWORD), accounts.verify
        )
    target = AccountRef(USER, Role.USER)
    rotate = C.API_KEYS_ROTATE_OWN
    assert decide(first, rotate, target, fresh=proof) == Allowed(rotate)
    assert decide(second, rotate, target) == Denied(E.REAUTHENTICATION_REQUIRED)
    assert decide(second, rotate, target, fresh=proof) == Denied(E.REAUTHENTICATION_FAILED)


def _account(client, headers, target, own=C.PROFILE_EDIT_OWN, other=C.ACCOUNTS_PROFILE_EDIT_OTHER):
    return client.get(
        f"/api/test-access/accounts/{target}",
        params={"own": own.value, "other": other.value},
        headers=headers,
    )


def test_account_targets_resolve_live_roles_without_enumeration(accounts, serve):
    client = serve()
    admin, user = accounts.cookie(ADMIN), accounts.key(USER)
    assert _account(client, admin, USER).json() == {"capability": "accounts.profile.edit.other"}
    assert _account(client, admin, ADMIN).json() == {"capability": "profile.edit.own"}
    assert _code(_account(client, admin, PEER_ADMIN)) == (403, "protected_account")
    assert _code(_account(client, admin, OWNER)) == (403, "protected_account")
    assert _code(_account(client, admin, 999)) == (404, "not_found")

    missing, existing = _account(client, user, 999), _account(client, user, PEER_USER)
    assert _code(missing) == _code(existing) == (404, "not_found")
    assert missing.json() == existing.json()
    assert _code(_account(client, user, OWNER)) == (404, "not_found")
    assert _account(client, user, USER).json() == {"capability": "profile.edit.own"}

    accounts.set_role(PEER_ADMIN, Role.USER)
    assert _account(client, admin, PEER_ADMIN).status_code == 200
    accounts.set_role(PEER_ADMIN, Role.ADMIN)
    assert _code(_account(client, admin, PEER_ADMIN)) == (403, "protected_account")
    accounts.set_override(ADMIN, C.ACCOUNTS_ADMINS_MANAGE, O.ALLOW)
    assert _account(client, admin, PEER_ADMIN).status_code == 200
    assert _code(_account(client, admin, OWNER)) == (403, "protected_account")


def test_resources_hide_other_owners_and_denied_media(accounts, serve):
    client = serve()
    pair = {"own": C.REVIEWS_DELETE_OWN.value, "other": C.REVIEWS_DELETE_OTHER.value}

    def review(headers, owner, **query):
        return client.get(
            f"/api/test-access/resources/{owner}", params={**pair, **query}, headers=headers
        )

    user, admin, owner = accounts.cookie(USER), accounts.key(ADMIN), accounts.key(OWNER)
    assert review(user, USER, media="allowed").json() == {"capability": "reviews.delete.own"}
    assert _code(review(user, PEER_USER, media="allowed")) == (404, "not_found")
    assert _code(review(user, PEER_USER, media="allowed", visible=True)) == (
        403,
        "capability_denied",
    )
    assert _code(review(user, USER, media="denied")) == (404, "not_found")
    assert _code(review(user, USER, media="denied", visible=True)) == (403, "media_unavailable")
    assert review(admin, OWNER, media="allowed").json() == {"capability": "reviews.delete.other"}
    assert _code(review(admin, USER, media="denied")) == (404, "not_found")
    assert review(owner, USER, media="denied").json() == {"capability": "reviews.delete.other"}


def test_corrupt_rows_never_grant_owner_powers_and_fail_closed(accounts, serve):
    client = serve()
    with sqlite3.connect(accounts.path) as database:
        database.executemany(
            "INSERT INTO role_capability_defaults (role, capability, granted, updated_at) "
            "VALUES (?, ?, ?, '2026-10-18')",
            [
                ("admin", "backups.download", 1),
                ("admin", "roles.defaults.edit", 1),
                ("user", "models.download", "yes"),
                ("user", "not.a.capability", 1),
            ],
        )
        database.executemany(
            "INSERT INTO user_capability_overrides (user_id, capability, effect, updated_at) "
            "VALUES (?, ?, ?, '2026-10-18')",
            [
                (ADMIN, "connections.edit", "allow"),
                (OWNER, "backups.download", "deny"),
                (OWNER, "models.download", "deny"),
            ],
        )
    admin = accounts.key(ADMIN)
    for capability in (C.BACKUPS_DOWNLOAD, C.CONNECTIONS_EDIT):
        assert _code(_instance(client, admin, capability)) == (403, "capability_denied")
    assert _code(_instance(client, accounts.cookie(USER), C.MODELS_DOWNLOAD)) == (
        403,
        "capability_denied",
    )
    owner = accounts.key(OWNER)
    for capability in (C.BACKUPS_DOWNLOAD, C.MODELS_DOWNLOAD):
        assert _instance(client, owner, capability).status_code == 200
    owner_payload = client.get("/api/test-access/payload", headers=accounts.cookie(OWNER)).json()
    assert len(owner_payload["capabilities"]) == len(C)


@pytest.mark.parametrize(
    "removed",
    [
        "reviews.import.server_path",
        "scans.start.server_path",
        "media_access.exceptions.edit",
        "media.identity.view",
        "profile.password.change.own",
        "accounts.local.create",
        "accounts.password.reset.other",
        "settings.registration.edit",
    ],
)
def test_stored_rows_for_a_removed_capability_change_nothing(accounts, serve, removed):
    client = serve()

    def payloads():
        return [
            client.get("/api/test-access/payload", headers=accounts.cookie(user)).json()
            for user in (USER, ADMIN)
        ]

    before = payloads()
    with sqlite3.connect(accounts.path) as database:
        database.executemany(
            "INSERT INTO role_capability_defaults (role, capability, granted, updated_at) "
            "VALUES (?, ?, ?, '2026-11-03')",
            [("user", removed, 1), ("admin", removed, 0)],
        )
        database.executemany(
            "INSERT INTO user_capability_overrides (user_id, capability, effect, updated_at) "
            "VALUES (?, ?, ?, '2026-11-03')",
            [(USER, removed, "allow"), (ADMIN, removed, "deny")],
        )
    assert payloads() == before
    assert all(removed not in payload["capabilities"] for payload in before)


def _fresh(client, headers, capability, password=None, account=None):
    return client.post(
        f"/api/test-access/fresh/{capability.value}",
        params={} if account is None else {"account": account},
        json={} if password is None else {"password": password},
        headers=headers,
    )


def test_fresh_actions_need_a_verified_session_password_never_an_api_key(accounts, serve):
    client = serve()
    restore = C.ROLES_DEFAULTS_EDIT
    owner_key, owner_session = accounts.key(OWNER), accounts.unsafe_cookie(OWNER)
    for password in (None, OWNER_PASSWORD):
        assert _code(_fresh(client, owner_key, restore, password)) == (
            403,
            "interactive_session_required",
        )
    assert _code(_fresh(client, owner_session, restore)) == (403, "reauthentication_required")
    for wrong in ("short", "a different owner password", MEMBER_PASSWORD):
        assert _code(_fresh(client, owner_session, restore, wrong)) == (
            403,
            "reauthentication_failed",
        )
    assert _fresh(client, owner_session, restore, OWNER_PASSWORD).json() == {
        "capability": "roles.defaults.edit"
    }

    link = C.ACCOUNTS_LINK_OWN
    admin_session = accounts.unsafe_cookie(ADMIN)
    assert _fresh(client, admin_session, link, MEMBER_PASSWORD, ADMIN).status_code == 200
    assert _code(_fresh(client, accounts.key(ADMIN), link, MEMBER_PASSWORD, ADMIN)) == (
        403,
        "interactive_session_required",
    )
    assert _code(_fresh(client, accounts.unsafe_cookie(USER), link, MEMBER_PASSWORD, USER)) == (
        403,
        "capability_denied",
    )
    assert _code(_fresh(client, admin_session, restore, MEMBER_PASSWORD)) == (
        403,
        "capability_denied",
    )


def test_workers_recheck_the_initiator_on_a_long_lived_session(accounts):
    with accounts.factory() as worker:

        def run(credential=CredentialKind.API_KEY):
            return authorize_job(worker, USER, credential, C.MODELS_DOWNLOAD, INSTANCE)

        assert run() == Allowed(C.MODELS_DOWNLOAD)
        accounts.set_override(USER, C.MODELS_DOWNLOAD, O.DENY)
        assert run() == Denied(E.CAPABILITY_DENIED)
        accounts.set_override(USER, C.MODELS_DOWNLOAD, O.INHERIT)
        assert run() == Allowed(C.MODELS_DOWNLOAD)
        accounts.set_status(USER, AccountStatus.DISABLED)
        assert run(CredentialKind.SESSION) == Denied(E.ACCOUNT_DISABLED)
        gone = authorize_job(worker, UserId(999), CredentialKind.SESSION, C.MODELS_DOWNLOAD)
        assert gone == Denied(E.AUTHENTICATION_REQUIRED)
        restore = authorize_job(worker, OWNER, CredentialKind.SESSION, C.ROLES_DEFAULTS_EDIT)
        assert restore == Denied(E.REAUTHENTICATION_REQUIRED)
    loaded = accounts.access(USER, CredentialKind.API_KEY)
    assert loaded.principal.account.status is AccountStatus.DISABLED
