# ruff: noqa: F811
"""MU-35 relinking after a server replacement, and the owner's protected credentials.

See `tests/account_link_support.py`. The boundary's `replaced` state serves a new
server ID; there Casey has a new user ID and Blake keeps his.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from scanner.db import JellyfinSettingsRow, UserRow
from scanner.formats.export_layout import JellyfinUserId
from scanner.integrations.jellyfin_user_snapshot import UserDeleted
from scanner.security.access.capability_ids import Capability
from scanner.security.access.identity import JellyfinServerId, ProviderIdentity
from scanner.security.access.link_state import PROFILE_ONLY, VERIFICATION_GRACE
from scanner.security.auth_repository import issue_session
from scanner.security.jellyfin_accounts import reads, store
from tests.account_link_support import (
    PASSWORDS,
    REPLACED_CASEY,
    audit_text,
    boundary,  # noqa: F401
    clock,  # noqa: F401
    confirm,
    identity,
    import_jellyfin_user,
    link_app,  # noqa: F401
    link_events,
    prove_and_link,
    publications,
    publish_both,
    request_proof,
    sync,
)
from tests.jellyfin_user_boundary import IDS, PRIMARY, REPLACEMENT, JellyfinBoundary
from tests.multi_user_access_support import (
    ADMIN,
    MEMBER_PASSWORD,
    ORIGIN,
    OWNER,
    OWNER_PASSWORD,
    PEER_ADMIN,
    PEER_USER,
    USER,
)
from tests.test_personal_exports_http import app, review_app  # noqa: F401
from tests.users_api_http_support import code


def _replace_server(app):
    app.boundary.state = "replaced"
    sync(app)


def _accounts(app) -> int:
    with app.accounts.factory() as db:
        return db.scalar(select(func.count()).select_from(UserRow))


def test_replacement_suspends_media_until_an_explicit_relink_never_by_name(link_app):
    publish_both(link_app, USER, "relinked")
    assert prove_and_link(link_app, ADMIN, USER, "Casey").status_code == 200
    linked = publications(link_app, USER)
    accounts = _accounts(link_app)

    _replace_server(link_app)
    assert identity(link_app, USER) == (PRIMARY, IDS["Casey"], "server_replaced")
    assert _accounts(link_app) == accounts
    assert publications(link_app, USER) == linked

    proven = request_proof(link_app, ADMIN, USER, "Casey", user_id=REPLACED_CASEY)
    assert proven.status_code == 200, proven.text
    preview = proven.json()["preview"]
    assert preview["kind"] == "relink"
    assert preview["identity"] == {
        "current": {"server_id": PRIMARY, "user_id": IDS["Casey"]},
        "after": {"server_id": REPLACEMENT, "user_id": REPLACED_CASEY},
    }
    assert preview["export_id"] == {"current": IDS["Casey"], "after": REPLACED_CASEY}
    assert preview["media"]["current"] == "suspended"
    assert preview["blockers"] == []
    relinked = confirm(link_app, ADMIN, USER, proven.json()["proof"], proven.json()["confirmation"])
    assert relinked.status_code == 200, relinked.text
    assert identity(link_app, USER) == (REPLACEMENT, REPLACED_CASEY, "verified")
    for old, new in zip(linked, publications(link_app, USER), strict=True):
        assert new[2] == REPLACED_CASEY and new[3].parent.name == REPLACED_CASEY
        assert new[3].read_bytes() == old[4]
        assert not old[3].exists()
    sync(link_app)
    assert identity(link_app, USER) == (REPLACEMENT, REPLACED_CASEY, "verified")
    assert _accounts(link_app) == accounts


def test_a_relink_to_the_same_user_id_changes_the_server_and_moves_no_file(link_app):
    publish_both(link_app, USER, "same-guid")
    assert prove_and_link(link_app, ADMIN, USER, "Blake").status_code == 200
    before = publications(link_app, USER)
    _replace_server(link_app)
    proven = request_proof(link_app, ADMIN, USER, "Blake")
    preview = proven.json()["preview"]
    assert (preview["kind"], preview["exports"], preview["blockers"]) == ("relink", [], [])
    assert preview["role"]["after"] == "user"
    relinked = confirm(link_app, ADMIN, USER, proven.json()["proof"], proven.json()["confirmation"])
    assert relinked.status_code == 200, relinked.text
    assert identity(link_app, USER) == (REPLACEMENT, IDS["Blake"], "verified")
    assert publications(link_app, USER) == before
    assert all(path.read_bytes() == content for *_, path, content, _rev in before)


def test_a_linked_account_cannot_switch_users_on_the_same_server(link_app):
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    proven = request_proof(link_app, ADMIN, USER, "Emery")
    assert proven.json()["preview"]["blockers"] == ["already_linked"]
    refused = confirm(link_app, ADMIN, USER, proven.json()["proof"], proven.json()["confirmation"])
    assert code(refused) == (409, "link_blocked")
    assert identity(link_app, USER) == (PRIMARY, IDS["Drew"], "verified")


def test_a_sync_read_of_the_previous_identity_never_applies_after_a_relink(link_app):
    assert prove_and_link(link_app, ADMIN, USER, "Casey").status_code == 200
    _replace_server(link_app)
    old = ProviderIdentity(JellyfinServerId(PRIMARY), JellyfinUserId(IDS["Casey"]))
    proven = request_proof(link_app, ADMIN, USER, "Casey", user_id=REPLACED_CASEY)
    assert (
        confirm(link_app, ADMIN, USER, proven.json()["proof"], proven.json()["confirmation"])
    ).status_code == 200
    with link_app.accounts.factory() as db:
        read = reads.begin_read(db, link_app.clock)
        late = store.apply(db, USER, UserDeleted(old, datetime.now(UTC)), read, identity=old)
    assert late is None
    assert identity(link_app, USER) == (REPLACEMENT, REPLACED_CASEY, "verified")
    with link_app.accounts.factory() as db:
        assert db.get(UserRow, USER).status == "active"


def test_the_owner_links_itself_and_keeps_its_name_and_full_access(link_app):
    assert request_proof(link_app, ADMIN, OWNER, "Avery").status_code == 403
    proven = request_proof(link_app, OWNER, OWNER, "Drew")
    assert proven.status_code == 200, proven.text
    preview = proven.json()["preview"]
    assert preview["role"] == {"current": "owner", "after": "owner"}
    assert preview["status"] == {"current": "active", "after": "active", "cause": None}
    linked = confirm(link_app, OWNER, OWNER, proven.json()["proof"], proven.json()["confirmation"])
    assert linked.status_code == 200, linked.text
    _replace_server(link_app)
    with link_app.accounts.factory() as db:
        owner = db.get(UserRow, OWNER)
        assert (owner.role, owner.status, owner.username) == ("owner", "active", "Drew")
    assert identity(link_app, OWNER) == (PRIMARY, IDS["Drew"], "server_replaced")
    access = link_app.accounts.access(OWNER)
    assert access.capabilities >= {Capability.OWNERSHIP_TRANSFER, Capability.ACCOUNTS_LINK_OWN}


def test_an_admin_linking_itself_to_an_ordinary_jellyfin_user_stays_admin(link_app):
    proven = request_proof(link_app, ADMIN, ADMIN, "Drew")
    assert proven.status_code == 200, proven.text
    assert proven.json()["preview"]["role"] == {"current": "admin", "after": "admin"}


def _relink_casey(app, actor, headers=None):
    proven = request_proof(app, actor, USER, "Casey", user_id=REPLACED_CASEY)
    assert proven.status_code == 200, proven.text
    body = proven.json()
    return body, confirm(app, actor, USER, body["proof"], body["confirmation"], headers=headers)


def test_relinking_another_account_rests_on_the_actors_attestation(link_app):
    publish_both(link_app, USER, "attested")
    assert prove_and_link(link_app, ADMIN, USER, "Casey").status_code == 200
    linked = publications(link_app, USER)
    _replace_server(link_app)

    proven, relinked = _relink_casey(link_app, ADMIN)
    assert proven["preview"]["proof_basis"] == "admin_attested"
    assert proven["preview"]["kind"] == "relink"
    assert relinked.status_code == 200, relinked.text
    assert relinked.json()["preview"]["proof_basis"] == "admin_attested"
    assert identity(link_app, USER) == (REPLACEMENT, REPLACED_CASEY, "verified")
    for old, new in zip(linked, publications(link_app, USER), strict=True):
        assert new[3].read_bytes() == old[4] and not old[3].exists()
    events = [event for event in link_events(link_app) if event[0] == "succeeded"]
    assert events[-2][3] == {"step": "proof", "basis": "admin_attested"}
    assert events[-1][3] | {} == {
        "step": "link",
        "basis": "admin_attested",
        "kind": "relink",
        "state": "removing",
        "exports": 2,
        "role": "user",
    }
    text = audit_text(link_app)
    assert "jf-casey-replacement-password" not in text and proven["proof"] not in text


def test_attestation_still_needs_fresh_authentication_and_the_new_identity(link_app):
    assert prove_and_link(link_app, ADMIN, USER, "Casey").status_code == 200
    _replace_server(link_app)
    body = {
        "reauthentication": {"password": "not the admin password"},
        "jellyfin": {"username": "Casey", "password": "jf-casey-replacement-password"},
    }
    headers = link_app.accounts.unsafe_cookie(ADMIN)
    stale = link_app.send("POST", f"/api/users/{USER}/link/proof", headers, body)
    assert code(stale)[1] == "reauthentication_failed"
    body["reauthentication"]["password"] = MEMBER_PASSWORD
    body["jellyfin"]["password"] = "wrong"
    wrong = link_app.send("POST", f"/api/users/{USER}/link/proof", headers, body)
    assert code(wrong) == (422, "jellyfin_proof_failed")
    assert identity(link_app, USER) == (PRIMARY, IDS["Casey"], "server_replaced")


def test_the_owner_relinks_an_admin_that_another_admin_cannot(link_app):
    linked = prove_and_link(link_app, OWNER, PEER_ADMIN, "Avery")
    assert linked.status_code == 200, linked.text
    _replace_server(link_app)
    assert request_proof(link_app, ADMIN, PEER_ADMIN, "Blake").status_code == 403
    proven = request_proof(link_app, OWNER, PEER_ADMIN, "Blake")
    assert proven.status_code == 200, proven.text
    assert proven.json()["preview"]["proof_basis"] == "admin_attested"
    body = proven.json()
    relinked = confirm(link_app, OWNER, PEER_ADMIN, body["proof"], body["confirmation"])
    assert relinked.status_code == 200, relinked.text
    assert identity(link_app, PEER_ADMIN) == (REPLACEMENT, IDS["Blake"], "verified")


def test_another_account_linked_on_the_same_server_cannot_be_linked_again(link_app):
    imported = import_jellyfin_user(link_app, "Drew")
    proven = request_proof(link_app, ADMIN, imported, "Emery")
    assert proven.status_code == 200, proven.text
    assert proven.json()["preview"]["blockers"] == ["already_linked"]
    body = proven.json()
    refused = confirm(link_app, ADMIN, imported, body["proof"], body["confirmation"])
    assert code(refused) == (409, "link_blocked")
    assert identity(link_app, imported)[1] == IDS["Drew"]


def test_a_relink_by_one_actor_voids_another_actors_attested_proof(link_app):
    assert prove_and_link(link_app, ADMIN, USER, "Casey").status_code == 200
    _replace_server(link_app)
    first = request_proof(link_app, ADMIN, USER, "Casey", user_id=REPLACED_CASEY).json()
    _owner_proof, relinked = _relink_casey(link_app, OWNER)
    assert relinked.status_code == 200, relinked.text
    late = confirm(link_app, ADMIN, USER, first["proof"], first["confirmation"])
    assert code(late) == (409, "link_proof_stale")


def test_an_unreachable_server_leaves_the_owner_in_charge_until_a_replacement(link_app):
    """The synthetic provider is stopped (connection refused) and never returns.

    The owner stays on the `live_accounts` stand-in server, which still confirms its
    password; an owner linked to the stopped server could not confirm one.
    """
    publish_both(link_app, USER, "unreachable")
    assert prove_and_link(link_app, ADMIN, USER, "Casey").status_code == 200
    assert prove_and_link(link_app, ADMIN, PEER_USER, "Drew").status_code == 200
    linked = publications(link_app, USER)
    link_app.boundary.__exit__()
    link_app.clock.now += VERIFICATION_GRACE + timedelta(hours=1)

    with link_app.accounts.factory() as db:
        issued = issue_session(
            db,
            remember_me=False,
            user_id=OWNER,
            now=link_app.clock.now,
            configuration=link_app.accounts.configuration,
        )
    owner = {
        **ORIGIN,
        "Cookie": f"editarr_session={issued.token}",
        "X-CSRF-Token": issued.csrf_token,
    }
    status = link_app.send("GET", "/api/auth/status", owner).json()
    assert status["state"] == "authenticated"
    assert set(status["access"]["capabilities"]) == set(Capability)
    for member in (USER, PEER_USER):
        assert link_app.accounts.access(member).capabilities <= PROFILE_ONLY
    body = {
        "reauthentication": {"password": OWNER_PASSWORD},
        "jellyfin": {"username": "Casey", "password": "jf-casey-replacement-password"},
    }
    unreachable = link_app.send("POST", f"/api/users/{USER}/link/proof", owner, body)
    assert code(unreachable) == (503, "jellyfin_unavailable")

    with JellyfinBoundary(secrets.token_urlsafe(24), PASSWORDS) as replacement:
        replacement.state = "replaced"
        with link_app.accounts.factory() as db:
            db.merge(
                JellyfinSettingsRow(id=1, server_url=replacement.url, api_key=replacement.api_key)
            )
            db.commit()
        proven = link_app.send("POST", f"/api/users/{USER}/link/proof", owner, body)
        assert proven.status_code == 200, proven.text
        assert proven.json()["preview"]["proof_basis"] == "admin_attested"
        relink = {
            "reauthentication": {"password": OWNER_PASSWORD},
            "proof": proven.json()["proof"],
            "confirmation": proven.json()["confirmation"],
        }
        relinked = link_app.send("POST", f"/api/users/{USER}/link", owner, relink)
        assert relinked.status_code == 200, relinked.text
        assert identity(link_app, USER) == (REPLACEMENT, REPLACED_CASEY, "verified")
        for old, new in zip(linked, publications(link_app, USER), strict=True):
            assert new[3].read_bytes() == old[4] and not old[3].exists()
        assert link_app.accounts.access(PEER_USER).capabilities <= PROFILE_ONLY
        assert not link_app.accounts.access(USER).capabilities <= PROFILE_ONLY
        assert link_app.send("POST", "/api/jellyfin/sync", owner, {}).status_code == 200
        assert identity(link_app, PEER_USER) == (PRIMARY, IDS["Drew"], "server_replaced")
        assert link_app.accounts.access(PEER_USER).capabilities <= PROFILE_ONLY
        assert identity(link_app, USER) == (REPLACEMENT, REPLACED_CASEY, "verified")
