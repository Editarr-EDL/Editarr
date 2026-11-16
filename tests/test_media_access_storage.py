"""MU-11: stored local scopes, and who may change them.

Runs against a fresh migrated database with real accounts, sessions, API keys,
role defaults, and password confirmation (`mu04-access-builders-3`), plus one
linked account for Casey from `editarr-jellyfin-users-1`.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import select

from scanner.db import UserProviderIdentityRow, UserRow
from scanner.formats.export_layout import new_local_export_id
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.security.access import link_state
from scanner.security.access.api_shapes import AccessErrorCode, Reauthentication
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.fresh import verify_reauthentication
from scanner.security.access.identity import CredentialKind, ProviderLinkState, UserId
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.records import MediaAllowed, MediaBasis
from scanner.security.access.vocabulary import Role
from scanner.security.media_access.keys import SourceRef
from scanner.security.media_access.management import (
    ScopeChoice,
    UnsupportedMediaAccessChange,
    set_default_scope,
    set_user_scope,
)
from scanner.security.media_access.matching import jellyfin_target
from scanner.security.media_access.resolver import OwnerSubject
from scanner.security.media_access.service import decide_media, load_subject
from tests.jellyfin_user_boundary import IDS, NOW, PRIMARY
from tests.media_access_support import (
    FAMILY,
    FAMILY_FILM,
    FEATURE,
    JELLYFIN_ITEMS,
    LATE_SHOW,
    MOVIES,
    catalog,
    jellyfin_user,
    libraries,
    resolve,
)
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
from tests.test_media_access_matrix import CASEY, JELLYFIN, Reader

LINKED = UserId(6)
FAMILY_SOURCE = SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, FAMILY)
MOVIES_SOURCE = SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, MOVIES)
SHOWS = "d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4d4"
SHOWS_SOURCE = SourceRef(MediaSourceKind.JELLYFIN_LIBRARY, SHOWS)
FAMILY_ITEM = JELLYFIN[FAMILY_FILM]
MOVIES_ITEM = JELLYFIN[FEATURE]
SHOWS_ITEM = jellyfin_target(
    PRIMARY, JELLYFIN_ITEMS[LATE_SHOW].model_copy(update={"library_id": SHOWS}), resolve
)
ITEMS = {"family": FAMILY_ITEM, "movies": MOVIES_ITEM, "shows": SHOWS_ITEM}


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    # Account loads judge link-state age on the same fixed clock as the decisions.
    monkeypatch.setattr(link_state, "link_clock", lambda: NOW)
    accounts = live_accounts(tmp_path)
    with accounts.factory() as db:
        db.add(
            UserRow(
                id=LINKED,
                role=Role.USER.value,
                status="active",
                username="casey",
                normalized_username="casey",
                local_export_id=new_local_export_id().value,
            )
        )
        db.flush()
        db.add(
            UserProviderIdentityRow(
                user_id=LINKED,
                server_id=PRIMARY,
                provider_user_id=IDS["Casey"],
                link_state=ProviderLinkState.VERIFIED.value,
                last_verified_at=NOW,
            )
        )
        db.commit()
    yield accounts
    accounts.close()


def _session(accounts, user_id):
    return accounts.access(user_id, CredentialKind.SESSION, accounts.sessions[user_id].id)


def _fresh(accounts, db, access):
    password = OWNER_PASSWORD if access.principal.user_id == OWNER else MEMBER_PASSWORD
    return verify_reauthentication(db, access, Reauthentication(password=password), accounts.verify)


def _as(accounts, user_id, change, *args, fresh=True, credential=CredentialKind.SESSION, **kwargs):
    """Run one management change as `user_id`, confirming the password when `fresh`."""

    with accounts.factory() as db:
        access = (
            _session(accounts, user_id)
            if credential is CredentialKind.SESSION
            else accounts.access(user_id, credential)
        )
        proof = _fresh(accounts, db, access) if fresh else None
        return change(db, access, *args, fresh=proof, **kwargs)


def _decisions(accounts, user_id, targets=tuple(ITEMS.values())):
    with accounts.factory() as db:
        subject = load_subject(db, accounts.access(user_id).principal.account)
    names = {value: name for name, value in IDS.items()}
    linked = getattr(subject, "identity", None)
    return decide_media(
        subject,
        list(targets),
        libraries=libraries(),
        user=jellyfin_user(names[linked.user_id.value]) if linked else None,
        catalog=catalog(),
        items=Reader(CASEY),
        now=NOW,
    )


def _revisions(accounts):
    with accounts.factory() as db:
        return dict(db.execute(select(UserRow.id, UserRow.permission_revision)).all())


def _reachable(accounts, user_id):
    decisions = _decisions(accounts, user_id)
    return {
        name for name, target in ITEMS.items() if isinstance(decisions[target.key], MediaAllowed)
    }


def test_default_changes_reach_inheriting_local_accounts_and_spare_individual_selections(
    accounts,
):
    assert _reachable(accounts, USER) == {"family", "movies", "shows"}
    movies_only = ScopeChoice(False, frozenset({MOVIES_SOURCE}))
    assert _as(accounts, OWNER, set_user_scope, PEER_USER, movies_only, now=NOW)
    before = _revisions(accounts)

    family_only = ScopeChoice(False, frozenset({FAMILY_SOURCE}))
    assert _as(accounts, ADMIN, set_default_scope, family_only, now=NOW)

    after = _revisions(accounts)
    assert {user for user in after if after[user] != before[user]} == {ADMIN, PEER_ADMIN, USER}
    assert _reachable(accounts, USER) == {"family"}
    assert _decisions(accounts, USER)[FAMILY_ITEM.key] == MediaAllowed(MediaBasis.LOCAL_DEFAULT)
    assert _reachable(accounts, PEER_USER) == {"movies"}
    assert _decisions(accounts, PEER_USER)[MOVIES_ITEM.key] == MediaAllowed(
        MediaBasis.LOCAL_OVERRIDE
    )

    assert not _as(
        accounts, ADMIN, set_default_scope, ScopeChoice(False, frozenset({FAMILY_SOURCE})), now=NOW
    )
    assert _revisions(accounts) == after, "an unchanged default bumps nobody"

    shows_only = ScopeChoice(False, frozenset({SHOWS_SOURCE}))
    _as(accounts, PEER_ADMIN, set_default_scope, shows_only, now=NOW)
    assert _reachable(accounts, USER) == {"shows"}
    assert _reachable(accounts, PEER_USER) == {"movies"}

    assert _as(accounts, ADMIN, set_user_scope, PEER_USER, None, now=NOW)
    assert _reachable(accounts, PEER_USER) == {"shows"}
    assert not _as(accounts, ADMIN, set_user_scope, PEER_USER, None, now=NOW)

    _as(accounts, ADMIN, set_user_scope, USER, ScopeChoice(True), now=NOW)
    _as(accounts, ADMIN, set_default_scope, ScopeChoice(False), now=NOW)
    assert _reachable(accounts, USER) == {"family", "movies", "shows"}
    assert _reachable(accounts, PEER_USER) == set()
    with accounts.factory() as db:
        assert isinstance(load_subject(db, accounts.access(OWNER).principal.account), OwnerSubject)


def test_admins_manage_local_access_until_the_owner_removes_it_through_role_defaults(accounts):
    choice = ScopeChoice(False, frozenset({FAMILY_SOURCE}))
    assert _as(accounts, ADMIN, set_user_scope, USER, choice, now=NOW)

    accounts.set_role_default(Role.ADMIN, Capability.MEDIA_ACCESS_USER_EDIT, False)
    accounts.set_role_default(Role.ADMIN, Capability.MEDIA_ACCESS_DEFAULTS_EDIT, False)
    for change, args in ((set_user_scope, (USER, None)), (set_default_scope, (choice,))):
        for admin in (ADMIN, PEER_ADMIN):
            with pytest.raises(AccessDenied) as denied:
                _as(accounts, admin, change, *args, now=NOW)
            assert denied.value.code is AccessErrorCode.CAPABILITY_DENIED
    assert _as(accounts, OWNER, set_user_scope, USER, None, now=NOW)

    accounts.set_role_default(Role.ADMIN, Capability.MEDIA_ACCESS_USER_EDIT, None)
    accounts.set_override(PEER_ADMIN, Capability.MEDIA_ACCESS_USER_EDIT, OverrideEffect.DENY)
    assert _as(accounts, ADMIN, set_user_scope, USER, choice, now=NOW)
    with pytest.raises(AccessDenied):
        _as(accounts, PEER_ADMIN, set_user_scope, USER, None, now=NOW)


@pytest.mark.parametrize(
    ("actor", "target", "options", "code"),
    [
        (ADMIN, USER, {"fresh": False}, AccessErrorCode.REAUTHENTICATION_REQUIRED),
        (
            ADMIN,
            USER,
            {"fresh": False, "credential": CredentialKind.API_KEY},
            AccessErrorCode.INTERACTIVE_SESSION_REQUIRED,
        ),
        (ADMIN, PEER_ADMIN, {}, AccessErrorCode.PROTECTED_ACCOUNT),
        (ADMIN, OWNER, {}, AccessErrorCode.PROTECTED_ACCOUNT),
        (USER, PEER_USER, {}, AccessErrorCode.NOT_FOUND),
        (USER, 99, {}, AccessErrorCode.NOT_FOUND),
        (ADMIN, 99, {}, AccessErrorCode.NOT_FOUND),
        (ADMIN, 99, {"fresh": False}, AccessErrorCode.REAUTHENTICATION_REQUIRED),
        (ADMIN, ADMIN, {}, AccessErrorCode.CAPABILITY_DENIED),
        (OWNER, OWNER, {}, AccessErrorCode.CAPABILITY_DENIED),
    ],
)
def test_scope_changes_need_fresh_authentication_and_respect_protected_accounts(
    accounts, actor, target, options, code
):
    before = _revisions(accounts)
    with pytest.raises(AccessDenied) as denied:
        _as(accounts, actor, set_user_scope, target, ScopeChoice(False), now=NOW, **options)
    assert denied.value.code is code
    assert _revisions(accounts) == before
    with sqlite3.connect(accounts.path) as database:
        assert database.execute("SELECT count(*) FROM media_access_scopes").fetchone() == (1,)


def test_a_linked_account_scope_change_is_refused_after_authorization(accounts):
    before = _revisions(accounts)
    with pytest.raises(UnsupportedMediaAccessChange) as refused:
        _as(accounts, OWNER, set_user_scope, LINKED, ScopeChoice(True), now=NOW)
    assert refused.value.code == "linked_account"
    assert _revisions(accounts) == before


def test_stored_rows_that_do_not_parse_grant_nothing(accounts):
    with sqlite3.connect(accounts.path) as database:
        database.execute("UPDATE media_access_scopes SET all_sources = 'yes' WHERE id = 1")
    assert _reachable(accounts, USER) == set()


def test_concurrent_first_selections_for_one_user_leave_one_complete_scope(accounts):
    from threading import Barrier, Thread

    from scanner.persistence.repository import media_access as storage

    choices = (
        {("jellyfin_library", FAMILY)},
        {("jellyfin_library", MOVIES), ("jellyfin_library", SHOWS)},
    )
    gate = Barrier(len(choices))
    results, errors = [], []

    def write(sources):
        with accounts.factory() as db:
            gate.wait()
            try:
                results.append(
                    storage.replace_scope(db, USER, all_sources=False, sources=sources, now=NOW)
                )
            except Exception as error:  # noqa: BLE001 - the test reports any writer failure
                errors.append(error)

    before = _revisions(accounts)[USER]
    threads = [Thread(target=write, args=(sources,)) for sources in choices]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert results == [True, True]
    with sqlite3.connect(accounts.path) as database:
        scopes = database.execute(
            "SELECT id FROM media_access_scopes WHERE user_id = ?", (USER,)
        ).fetchall()
        stored = set(
            database.execute(
                "SELECT source_kind, source_id FROM media_access_scope_sources WHERE scope_id = ?",
                scopes[0],
            ).fetchall()
        )
    assert len(scopes) == 1
    assert stored in choices
    assert _revisions(accounts)[USER] == before + 2
