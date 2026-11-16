# ruff: noqa: F811
"""MU-35 proof binding: account changes, switch-time rechecks, refusal audit, avatar.

Ports the round 1 review probes for F1, F4, and F5 (web/test-results/mu-35-review).
See `tests/account_link_support.py` for the accounts, exports, and synthetic Jellyfin.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from scanner.db import UserRow
from scanner.integrations.jellyfin_user_snapshot import parse_user
from scanner.persistence.repository import export_identity_moves as files
from scanner.security import account_repository as accounts
from scanner.security.access.identity import (
    DisableCause,
    JellyfinServerId,
    ProviderIdentity,
    UserId,
)
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.account_avatars import decode_avatar
from scanner.security.account_links.plan import build_plan
from scanner.security.account_links.transition import SwitchRefusal
from scanner.security.audit.actions import ActorKind, AuditAction
from scanner.security.audit.checks import check_event
from tests.account_link_support import (
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
    transitions,
)
from tests.jellyfin_user_boundary import IDS, PRIMARY, REPLACEMENT, user_dto
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app, review_app  # noqa: F401
from tests.users_api_http_support import code, png


def _disable(app) -> None:
    with app.accounts.factory() as db:
        accounts.set_disable_cause(db, USER, DisableCause.LOCAL, now=app.clock())
        db.commit()


def _raise_revision(app) -> None:
    with app.accounts.factory() as db:
        db.get(UserRow, USER).permission_revision += 1
        db.commit()


CHANGES = {"disable": _disable, "revision": _raise_revision}


def _unchanged(app, before) -> None:
    assert identity(app, USER) is None
    assert publications(app, USER) == before
    assert all(path.read_bytes() == content for *_, path, content, _rev in before)
    assert not any(app.media.rglob(f"*{IDS['Drew']}*"))


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_status_or_revision_change_voids_an_outstanding_proof(link_app, change):
    proven = request_proof(link_app, ADMIN, USER, "Drew").json()
    CHANGES[change](link_app)
    stale = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(stale) == (409, "link_proof_stale")
    again = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(again) == (409, "link_proof_invalid")
    assert identity(link_app, USER) is None
    assert link_events(link_app)[-2][0:3] == ("denied", ADMIN, USER)
    assert link_events(link_app)[-2][3] == {
        "step": "link",
        "basis": "admin_attested",
        "reason": "link_proof_stale",
    }
    assert link_events(link_app)[-1][3] == {"step": "link", "reason": "link_proof_invalid"}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_change_while_files_are_written_rolls_back_and_keeps_old_exports(
    link_app, monkeypatch, change
):
    publish_both(link_app, USER, f"during-{change}")
    before = publications(link_app, USER)
    real = files.write_new
    changed = []

    def write_then_change(move, new_id):
        staged = real(move, new_id)
        if not changed:
            changed.append(True)
            CHANGES[change](link_app)
        return staged

    monkeypatch.setattr(files, "write_new", write_then_change)
    response = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert code(response) == (409, "link_proof_stale")
    _unchanged(link_app, before)
    assert transitions(link_app, USER) == [("rolled_back", "refused", [None, None])]
    assert link_events(link_app)[-1][3] == {
        "step": "link",
        "basis": "admin_attested",
        "reason": "link_proof_stale",
    }


def test_a_proof_from_another_session_of_the_same_actor_is_refused(link_app):
    from scanner.security.auth_repository_sessions import issue_session

    proven = request_proof(link_app, ADMIN, USER, "Drew").json()
    with link_app.accounts.factory() as db:
        session = issue_session(
            db, remember_me=False, user_id=ADMIN, configuration=link_app.accounts.configuration
        )
    headers = dict(
        link_app.accounts.unsafe_cookie(ADMIN), Cookie=f"editarr_session={session.token}"
    )
    headers["X-CSRF-Token"] = session.csrf_token
    refused = confirm(
        link_app, ADMIN, USER, proven["proof"], proven["confirmation"], headers=headers
    )
    assert code(refused) == (409, "link_proof_invalid")
    assert identity(link_app, USER) is None


def test_an_identity_imported_while_files_are_written_is_an_audited_conflict(link_app, monkeypatch):
    publish_both(link_app, USER, "concurrent-import")
    before = publications(link_app, USER)
    real = files.write_new
    imported = []

    def write_then_import(move, new_id):
        staged = real(move, new_id)
        if not imported:
            imported.append(import_jellyfin_user(link_app, "Drew"))
        return staged

    monkeypatch.setattr(files, "write_new", write_then_import)
    response = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert code(response) == (409, "jellyfin_identity_in_use")
    _unchanged(link_app, before)
    assert identity(link_app, imported[0])[1] == IDS["Drew"]
    assert transitions(link_app, USER) == [("rolled_back", "refused", [None, None])]
    assert link_events(link_app)[-1] == (
        "denied",
        ADMIN,
        USER,
        {"step": "link", "basis": "admin_attested", "reason": "jellyfin_identity_in_use"},
    )


LINK_ROUTE_REFUSALS = {
    "account_proof_failed",
    "account_proof_unavailable",
    "jellyfin_proof_failed",
    "jellyfin_unavailable",
    "link_proof_invalid",
    "link_proof_expired",
    "link_proof_stale",
    "link_blocked",
    "link_preview_stale",
    "write_failed",
}


@pytest.mark.parametrize("reason", sorted({r.value for r in SwitchRefusal} | LINK_ROUTE_REFUSALS))
def test_every_link_refusal_is_an_allowed_account_link_audit_reason(reason):
    for step in ("proof", "link"):
        event = AuditEvent(
            datetime.now(UTC),
            UserId(ADMIN),
            None,
            AuditAction.ACCOUNT_LINK,
            AuditOutcome.DENIED,
            target_user=UserId(USER),
            details={"step": step, "reason": reason},
        )
        assert check_event(event, ActorKind.USER) is AuditAction.ACCOUNT_LINK


def test_linking_to_an_identity_without_an_avatar_removes_the_local_avatar(link_app):
    with link_app.accounts.factory() as db:
        accounts.set_avatar(db, USER, decode_avatar(png()), now=link_app.clock())
        db.commit()
    proven = request_proof(link_app, ADMIN, USER, "Drew").json()
    assert proven["preview"]["profile"]["avatar"] == "none"
    linked = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert linked.status_code == 200, linked.text
    with link_app.accounts.factory() as db:
        assert accounts.read_avatar(db, USER) is None
    sync(link_app)
    with link_app.accounts.factory() as db:
        assert accounts.read_avatar(db, USER) is None


@pytest.mark.parametrize("variant", ["upper", "dashed", "dashed-upper"])
def test_an_imported_identity_blocks_every_encoding_of_its_guid(link_app, variant):
    import_jellyfin_user(link_app, "Drew")
    raw = IDS["Drew"]
    if "dashed" in variant:
        raw = "-".join((raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:]))
    if "upper" in variant:
        raw = raw.upper()
    snapshot = parse_user(user_dto("Drew") | {"Id": raw}, PRIMARY, link_app.clock())
    with link_app.accounts.factory() as db:
        assert "jellyfin_identity_in_use" in build_plan(db, USER, snapshot).blockers


def test_the_same_user_id_on_another_server_is_another_identity(link_app):
    import_jellyfin_user(link_app, "Drew")
    snapshot = parse_user(user_dto("Drew"), PRIMARY, link_app.clock())
    other = ProviderIdentity(JellyfinServerId(REPLACEMENT), snapshot.identity.user_id)
    with link_app.accounts.factory() as db:
        blockers = build_plan(db, USER, replace(snapshot, identity=other)).blockers
    assert "jellyfin_identity_in_use" not in blockers
