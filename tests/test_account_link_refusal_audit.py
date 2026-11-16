# ruff: noqa: F811
"""MU-35 link-step refusal audits: a refusal names its step, and its proof basis once
the request established it; an access refusal under the switch lock rolls back as
`refused`; a provider refusal after a valid proof is audited once.

Ports the round 2 review probes for G2 and G3 (web/test-results/mu-35-review-2) and
the round 3 probe for H2 (web/test-results/mu-35-review-3).
"""

from __future__ import annotations

import pytest

from scanner.db import JellyfinSettingsRow, UserRow
from scanner.persistence.repository import export_identity_moves as files
from tests.account_link_support import (
    REPLACED_CASEY,
    boundary,  # noqa: F401
    clock,  # noqa: F401
    confirm,
    identity,
    link_app,  # noqa: F401
    link_events,
    prove_and_link,
    publications,
    publish_both,
    request_proof,
    sync,
    transitions,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app, review_app  # noqa: F401
from tests.users_api_http_support import code


def _after_first_write(monkeypatch, change) -> None:
    real = files.write_new
    done = []

    def write_then_change(move, new_id):
        staged = real(move, new_id)
        if not done:
            done.append(True)
            change()
        return staged

    monkeypatch.setattr(files, "write_new", write_then_change)


def test_an_attested_relink_refused_during_writes_audits_its_basis(link_app, monkeypatch):
    publish_both(link_app, USER, "attested-refusal")
    assert prove_and_link(link_app, ADMIN, USER, "Casey").status_code == 200
    link_app.boundary.state = "replaced"
    sync(link_app)
    before = publications(link_app, USER)

    def change_revision():
        with link_app.accounts.factory() as db:
            db.get(UserRow, USER).permission_revision += 1
            db.commit()

    _after_first_write(monkeypatch, change_revision)
    proven = request_proof(link_app, ADMIN, USER, "Casey", user_id=REPLACED_CASEY).json()
    assert proven["preview"]["proof_basis"] == "admin_attested"
    refused = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(refused) == (409, "link_proof_stale")
    assert link_events(link_app)[-1] == (
        "denied",
        ADMIN,
        USER,
        {"step": "link", "basis": "admin_attested", "reason": "link_proof_stale"},
    )
    assert publications(link_app, USER) == before
    assert transitions(link_app, USER)[-1] == ("rolled_back", "refused", [None, None])


def test_a_target_promoted_during_writes_is_a_refused_link_step(link_app, monkeypatch):
    publish_both(link_app, USER, "role-change")
    before = publications(link_app, USER)

    def promote():
        with link_app.accounts.factory() as db:
            db.get(UserRow, USER).role = "admin"
            db.commit()

    _after_first_write(monkeypatch, promote)
    response = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert code(response) == (403, "protected_account")
    assert identity(link_app, USER) is None
    assert publications(link_app, USER) == before
    assert all(path.read_bytes() == content for *_, path, content, _rev in before)
    assert transitions(link_app, USER) == [("rolled_back", "refused", [None, None])]
    assert link_events(link_app)[-1] == (
        "denied",
        ADMIN,
        USER,
        {"step": "link", "basis": "admin_attested", "reason": "protected_account"},
    )


def test_an_unknown_proof_is_refused_without_a_basis(link_app):
    proven = request_proof(link_app, ADMIN, USER, "Drew").json()
    refused = confirm(link_app, ADMIN, USER, "not-a-proof", proven["confirmation"])
    assert code(refused) == (409, "link_proof_invalid")
    assert link_events(link_app)[-1][3] == {"step": "link", "reason": "link_proof_invalid"}


def _unconfigure(app) -> None:
    with app.accounts.factory() as db:
        settings = db.get(JellyfinSettingsRow, 1)
        settings.server_url = settings.api_key = None
        db.commit()


def _fail_with_503(app) -> None:
    faults = app.boundary.fixture["faults"]
    app.boundary.fixture = {
        **app.boundary.fixture,
        "faults": {
            **faults,
            "server_error": {
                "kind": "response",
                "status": 503,
                "contentType": "text/plain",
                "body": "offline",
            },
        },
    }
    app.boundary.fault = "server_error"


def _disable_every_user(app) -> None:
    real = app.boundary.users

    def disabled():
        users = real()
        for user in users:
            user["Policy"]["IsDisabled"] = True
        return users

    app.boundary.users = disabled


PROVIDER_FAULTS = {
    "unconfigured": (_unconfigure, 503, "jellyfin_unavailable"),
    "outage": (_fail_with_503, 503, "jellyfin_unavailable"),
    "disabled": (_disable_every_user, 409, "jellyfin_user_unavailable"),
}


@pytest.mark.parametrize("attested", [False, True])
@pytest.mark.parametrize("fault", PROVIDER_FAULTS)
def test_a_provider_refusal_after_a_valid_proof_is_audited_once(link_app, fault, attested):
    if attested:
        assert prove_and_link(link_app, ADMIN, USER, "Casey").status_code == 200
        link_app.boundary.state = "replaced"
        sync(link_app)
        proven = request_proof(link_app, ADMIN, USER, "Casey", user_id=REPLACED_CASEY).json()
    else:
        proven = request_proof(link_app, ADMIN, USER, "Drew").json()
    basis = proven["preview"]["proof_basis"]
    assert basis == "admin_attested"
    before, linked = link_events(link_app), identity(link_app, USER)
    inject, status, reason = PROVIDER_FAULTS[fault]
    inject(link_app)

    refused = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(refused) == (status, reason), refused.text
    assert identity(link_app, USER) == linked
    assert link_events(link_app)[len(before) :] == [
        ("denied", ADMIN, USER, {"step": "link", "basis": basis, "reason": reason})
    ]
