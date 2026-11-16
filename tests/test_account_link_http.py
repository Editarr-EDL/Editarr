# ruff: noqa: F811
"""MU-35 linking over HTTP: proofs, preview, export moves, and identity changes.

See `tests/account_link_support.py` for the accounts, exports, and synthetic Jellyfin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scanner.db import JellyfinUserBlockRow, MediaItemExportRow, UserRow
from scanner.security.account_links import transition
from tests.account_link_support import (
    audit_text,
    boundary,  # noqa: F401
    clock,  # noqa: F401
    confirm,
    identity,
    import_jellyfin_user,
    jellyfin,
    link_app,  # noqa: F401
    link_events,
    publications,
    publish_both,
    request_proof,
    transitions,
)
from tests.jellyfin_user_boundary import IDS, PRIMARY
from tests.multi_user_access_support import (
    ADMIN,
    MEMBER_PASSWORD,
    OWNER,
    PEER_ADMIN,
    PEER_USER,
    USER,
)
from tests.test_personal_exports_http import app, review_app  # noqa: F401
from tests.users_api_http_support import code


def test_linking_moves_both_formats_and_removes_old_files_after_the_switch(link_app):
    publish_both(link_app, USER, "feature")
    before = publications(link_app, USER)
    assert {export_id.startswith("local-") for _, _, export_id, *_ in before} == {True}

    proven = request_proof(link_app, ADMIN, USER, "Drew")
    assert proven.status_code == 200, proven.text
    preview = proven.json()["preview"]
    assert preview["kind"] == "link"
    assert preview["identity"] == {
        "current": None,
        "after": {"server_id": PRIMARY, "user_id": IDS["Drew"]},
    }
    assert preview["export_id"]["after"] == IDS["Drew"]
    assert preview["role"] == {"current": "user", "after": "user"}
    assert preview["media"]["current"] == "local"
    assert [move["action"] for move in preview["exports"]] == ["move", "move"]
    assert preview["blockers"] == []
    assert preview["proof_basis"] == "admin_attested"
    assert preview["status"] == {"current": "active", "after": "active", "cause": None}
    assert identity(link_app, USER) is None

    linked = confirm(link_app, ADMIN, USER, proven.json()["proof"], proven.json()["confirmation"])
    assert linked.status_code == 200, linked.text
    assert linked.json()["state"] == "completed"
    assert identity(link_app, USER) == (PRIMARY, IDS["Drew"], "verified")
    after = publications(link_app, USER)
    for old, new in zip(before, after, strict=True):
        assert new[2] == IDS["Drew"]
        assert new[3].name.endswith(f".user-{IDS['Drew']}.{new[1]}")
        assert new[3].parent.name == IDS["Drew"]
        assert new[3].read_bytes() == new[4] == old[4]
        assert new[5] == old[5] + 1
        assert not old[3].exists()
        assert not old[3].parent.exists()
    assert transitions(link_app, USER) == [("completed", None, ["removed", "removed"])]
    events = link_events(link_app)
    assert [
        (outcome, actor, target, details["step"]) for outcome, actor, target, details in events
    ] == [
        ("succeeded", ADMIN, USER, "proof"),
        ("succeeded", ADMIN, USER, "link"),
    ]
    assert events[0][3] == {"step": "proof", "basis": "admin_attested"}
    assert events[-1][3] | {} == {
        "step": "link",
        "basis": "admin_attested",
        "kind": "link",
        "state": "removing",
        "exports": 2,
        "role": "user",
    }
    text = audit_text(link_app)
    assert MEMBER_PASSWORD not in text
    assert "jf-drew-password" not in text
    assert proven.json()["proof"] not in text


def _proof(app, actor=ADMIN, target=USER, name="Drew", **kwargs):
    response = request_proof(app, actor, target, name, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_proof_works_once_and_only_for_its_actor_session_and_target(link_app):
    other_target = _proof(link_app, target=PEER_USER, name="Emery")
    proven = _proof(link_app)
    token, confirmation = proven["proof"], proven["confirmation"]

    replayed = confirm(link_app, ADMIN, PEER_USER, token, other_target["confirmation"])
    assert code(replayed) == (409, "link_proof_invalid")
    assert code(confirm(link_app, ADMIN, USER, token, confirmation)) == (409, "link_proof_invalid")

    proven = _proof(link_app)
    stolen = confirm(link_app, PEER_ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(stolen) == (409, "link_proof_invalid")
    assert code(confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])) == (
        409,
        "link_proof_invalid",
    )

    proven = _proof(link_app)
    linked = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert linked.status_code == 200, linked.text
    again = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(again) == (409, "link_proof_invalid")
    assert identity(link_app, PEER_USER) is None
    reasons = [details.get("reason") for _o, _a, _t, details in link_events(link_app)]
    assert reasons.count("link_proof_invalid") == 5


def test_a_proof_expires_after_ten_minutes(link_app):
    proven = _proof(link_app)
    link_app.clock.now += timedelta(minutes=10)
    expired = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(expired) == (409, "link_proof_expired")
    assert identity(link_app, USER) is None
    assert code(confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])) == (
        409,
        "link_proof_invalid",
    )


def test_a_matching_name_or_a_wrong_password_proves_nothing(link_app):
    with link_app.accounts.factory() as db:
        user = db.get(UserRow, USER)
        user.username = user.normalized_username = "drew"
        db.commit()
    bad = link_app.send(
        "POST",
        f"/api/users/{USER}/link/proof",
        link_app.accounts.unsafe_cookie(ADMIN),
        {
            "reauthentication": {"password": MEMBER_PASSWORD},
            "jellyfin": {"username": "Drew", "password": "drew"},
        },
    )
    assert code(bad) == (422, "jellyfin_proof_failed")
    assert identity(link_app, USER) is None
    text = audit_text(link_app)
    assert '"drew"' not in text and "jf-drew-password" not in text


def test_a_local_account_password_is_no_longer_accepted(link_app):
    refused = link_app.send(
        "POST",
        f"/api/users/{USER}/link/proof",
        link_app.accounts.unsafe_cookie(ADMIN),
        {
            "reauthentication": {"password": MEMBER_PASSWORD},
            "account_password": MEMBER_PASSWORD,
            "jellyfin": jellyfin("Drew"),
        },
    )
    assert refused.status_code == 422
    assert identity(link_app, USER) is None


def test_failed_proofs_are_throttled_per_target(link_app):
    wrong = {"username": "Drew", "password": "wrong"}
    for _ in range(10):
        response = link_app.send(
            "POST",
            f"/api/users/{USER}/link/proof",
            link_app.accounts.unsafe_cookie(ADMIN),
            {"reauthentication": {"password": MEMBER_PASSWORD}, "jellyfin": wrong},
        )
        if response.status_code == 429:
            break
        assert code(response) == (422, "jellyfin_proof_failed")
    assert code(response) == (429, "link_throttled")
    assert code(request_proof(link_app, ADMIN, USER, "Drew")) == (429, "link_throttled")


def test_an_existing_imported_account_blocks_linking_until_resolved(link_app):
    imported = import_jellyfin_user(link_app, "Drew")
    assert identity(link_app, imported)[1] == IDS["Drew"]
    publish_both(link_app, USER, "blocked")
    before = publications(link_app, USER)
    proven = _proof(link_app)
    assert proven["preview"]["blockers"] == ["jellyfin_identity_in_use"]
    refused = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(refused) == (409, "link_blocked")
    assert refused.json()["preview"]["blockers"] == ["jellyfin_identity_in_use"]
    assert identity(link_app, USER) is None
    assert publications(link_app, USER) == before
    assert all(path.read_bytes() == content for *_, path, content, _rev in before)


def test_a_foreign_file_at_the_new_path_blocks_linking_untouched(link_app):
    publish_both(link_app, USER, "collision")
    before = publications(link_app, USER)
    foreign = (
        before[0][3].parent.parent
        / IDS["Drew"]
        / before[0][3].name.replace(before[0][2], IDS["Drew"])
    )
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"not editarr's\n")
    proven = _proof(link_app)
    assert proven["preview"]["blockers"] == ["export_file_collision"]
    assert [move["conflict"] for move in proven["preview"]["exports"]] == [
        "export_file_collision",
        None,
    ]
    assert code(confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])) == (
        409,
        "link_blocked",
    )
    assert foreign.read_bytes() == b"not editarr's\n"
    assert publications(link_app, USER) == before
    assert identity(link_app, USER) is None


def test_a_change_after_the_preview_asks_for_a_new_confirmation(link_app):
    publish_both(link_app, USER, "first")
    proven = _proof(link_app)
    publish_both(link_app, USER, "second")
    stale = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert code(stale) == (409, "link_preview_stale")
    assert len(stale.json()["preview"]["exports"]) == 4
    assert identity(link_app, USER) is None
    linked = confirm(link_app, ADMIN, USER, proven["proof"], stale.json()["confirmation"])
    assert linked.status_code == 200, linked.text
    assert {row[2] for row in publications(link_app, USER)} == {IDS["Drew"]}


def test_an_api_key_cannot_prove_or_link(link_app):
    body = {"reauthentication": {"password": MEMBER_PASSWORD}, "jellyfin": jellyfin("Drew")}
    keyed = link_app.send(
        "POST", f"/api/users/{USER}/link/proof", link_app.accounts.key(ADMIN), body
    )
    assert code(keyed) == (403, "interactive_session_required")
    proven = _proof(link_app)
    keyed = confirm(
        link_app,
        ADMIN,
        USER,
        proven["proof"],
        proven["confirmation"],
        headers=link_app.accounts.key(ADMIN),
    )
    assert code(keyed)[1] == "interactive_session_required"
    assert identity(link_app, USER) is None


def test_ordinary_users_cannot_link_and_admins_cannot_link_admins_or_the_owner(link_app):
    owner = identity(link_app, OWNER)
    assert request_proof(link_app, USER, USER, "Drew").status_code == 403
    assert request_proof(link_app, USER, PEER_USER, "Drew").status_code == 404
    assert request_proof(link_app, ADMIN, PEER_ADMIN, "Drew").status_code == 403
    assert request_proof(link_app, ADMIN, OWNER, "Drew").status_code == 403
    assert all(identity(link_app, user) is None for user in (USER, PEER_USER, PEER_ADMIN))
    assert identity(link_app, OWNER) == owner


def test_linking_to_a_jellyfin_administrator_keeps_the_accounts_role(link_app):
    proven = _proof(link_app, name="Avery")
    assert proven["preview"]["role"] == {"current": "user", "after": "user"}
    linked = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert linked.status_code == 200, linked.text
    with link_app.accounts.factory() as db:
        assert db.get(UserRow, USER).role == "user"


def test_linking_replaces_the_local_username_with_the_jellyfin_name(link_app):
    proven = _proof(link_app, name="Drew")
    linked = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert linked.status_code == 200, linked.text
    with link_app.accounts.factory() as db:
        assert (db.get(UserRow, USER).username, db.get(UserRow, USER).normalized_username) == (
            "Drew",
            "drew",
        )


def _block(app, name):
    with app.accounts.factory() as db:
        db.add(
            JellyfinUserBlockRow(
                server_id=PRIMARY,
                provider_user_id=IDS[name],
                name=name,
                blocked_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
        db.commit()


def test_a_blocked_jellyfin_user_cannot_be_linked(link_app):
    _block(link_app, "Drew")
    proven = _proof(link_app, name="Drew")
    assert "jellyfin_user_blocked" in proven["preview"]["blockers"]
    refused = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])
    assert (refused.status_code, refused.json()["code"]) == (409, "link_blocked")
    assert identity(link_app, USER) is None
    assert [
        (outcome, details["step"], details.get("reason"))
        for outcome, _, _, details in (link_events(link_app))
    ] == [
        ("succeeded", "proof", None),
        ("denied", "proof", "link_blocked"),
        ("denied", "link", "link_blocked"),
    ]


def test_a_block_added_while_new_files_are_written_refuses_the_switch(link_app, monkeypatch):
    publish_both(link_app, USER, "raced")
    before = publications(link_app, USER)
    proven = _proof(link_app, name="Drew")
    assert proven["preview"]["blockers"] == []
    original = transition.write_files

    def write_then_block(*args, **kwargs):
        original(*args, **kwargs)
        _block(link_app, "Drew")

    monkeypatch.setattr(transition, "write_files", write_then_block)
    refused = confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])

    assert code(refused) == (409, "jellyfin_user_blocked")
    assert identity(link_app, USER) is None
    assert publications(link_app, USER) == before
    assert transitions(link_app, USER)[-1][0] == "rolled_back"
    outcome, _, _, details = link_events(link_app)[-1]
    assert (outcome, details["step"], details["reason"]) == (
        "denied",
        "link",
        "jellyfin_user_blocked",
    )


def test_another_publication_recorded_at_the_new_path_blocks_linking(link_app):
    publish_both(link_app, USER, "mine")
    publish_both(link_app, PEER_USER, "theirs")
    before = publications(link_app, USER)
    edl = before[0][3]
    target = edl.parent.parent / IDS["Drew"] / edl.name.replace(before[0][2], IDS["Drew"])
    other = publications(link_app, PEER_USER)[0][0]
    with link_app.accounts.factory() as db:
        db.get(MediaItemExportRow, other).destination_path = str(target)
        db.commit()
    proven = _proof(link_app)
    assert proven["preview"]["blockers"] == ["export_filename_collision"]
    assert code(confirm(link_app, ADMIN, USER, proven["proof"], proven["confirmation"])) == (
        409,
        "link_blocked",
    )
    assert publications(link_app, USER) == before
