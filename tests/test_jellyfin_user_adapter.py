"""Jellyfin user adapter identity and access against the jellyfin-users-v1 boundary.

A local HTTP server serves synthetic Jellyfin 12.0.0 shapes. These tests prove
the adapter's handling of those shapes, not live Jellyfin behavior.
"""

from __future__ import annotations

import dataclasses
import hashlib
import secrets

import httpx
import pytest

from scanner.integrations.jellyfin_user_snapshot import (
    CredentialProof,
    ItemAccess,
    ItemAccessSnapshot,
    JellyfinUserSnapshot,
    SessionCleanup,
    Unverified,
    UnverifiedReason,
    UserAvatar,
    UserDeleted,
    parse_user,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from tests.jellyfin_user_boundary import (
    AVATAR_BYTES,
    FAMILY_FILM,
    FEATURE,
    FIXTURE,
    FIXTURE_PATH,
    IDS,
    LATE_SHOW,
    MATURE,
    NOW,
    PRIMARY,
    REPLACEMENT,
    JellyfinBoundary,
    identity,
    scripted_transport,
    sign_in_result,
    user_dto,
)

MISSING = object()

LIBRARIES = FIXTURE["libraries"]
FIXTURE_SHA256 = "c4fd21e52fa957d1e5794b4de53305ea35b095a53a673ca2a32e7b7c02d9af29"


def test_fixture_bytes_match_the_pinned_version():
    assert FIXTURE["fixture"] == "editarr-jellyfin-users-1"
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == FIXTURE_SHA256


@pytest.fixture
def passwords() -> dict[str, str]:
    return {user_id: secrets.token_urlsafe(18) for user_id in IDS.values()}


@pytest.fixture
def boundary(passwords):
    with JellyfinBoundary(secrets.token_urlsafe(24), passwords) as server:
        yield server


@pytest.fixture
def adapter(boundary):
    with JellyfinUserAdapter(boundary.url, boundary.api_key, clock=lambda: NOW) as adapter:
        yield adapter


def test_credential_proof_returns_the_server_bound_identity_and_ends_the_session(
    adapter, boundary, passwords
):
    proof = adapter.authenticate("casey", passwords[IDS["Casey"]])

    assert isinstance(proof, CredentialProof)
    assert proof.session == SessionCleanup.ENDED
    assert proof.user.identity == identity("Casey")
    assert proof.user.name == "Casey"
    assert proof.user.verified_at == NOW
    assert len(boundary.issued_tokens) == 1
    assert boundary.open_tokens == set()


@pytest.mark.parametrize(
    ("state", "username", "owner"),
    [
        ("baseline", "Casey", "Blake"),
        ("baseline", "Nobody", "Casey"),
        ("changed", "Drew", "Drew"),
    ],
    ids=["wrong-password", "unknown-name", "disabled"],
)
def test_failed_sign_in_is_rejected_without_naming_or_deleting_a_user(
    adapter, boundary, passwords, state, username, owner
):
    boundary.state = state

    outcome = adapter.authenticate(username, passwords[IDS[owner]])

    assert outcome == Unverified(UnverifiedReason.CREDENTIALS_REJECTED)
    assert boundary.issued_tokens == []


def test_a_reused_name_signs_in_as_the_account_that_now_holds_it(adapter, boundary, passwords):
    boundary.state = "changed"

    blake = adapter.authenticate("Casey", passwords[IDS["Blake"]])
    former_casey = adapter.authenticate("Casey", passwords[IDS["Casey"]])
    renamed = adapter.verify_user(identity("Casey"))

    assert isinstance(blake, CredentialProof)
    assert blake.user.identity == identity("Blake")
    assert blake.user.name == "Casey"
    assert former_casey == Unverified(UnverifiedReason.CREDENTIALS_REJECTED)
    assert isinstance(renamed, JellyfinUserSnapshot)
    assert (renamed.identity, renamed.name) == (identity("Casey"), "Casey Archive")


def test_administrator_status_follows_upstream_changes(adapter, boundary):
    def roles() -> dict[str, bool]:
        return {
            name: adapter.verify_user(identity(name)).is_administrator
            for name in ("Avery", "Blake")
        }

    before = roles()
    boundary.state = "changed"

    assert before == {"Avery": True, "Blake": False}
    assert roles() == {"Avery": False, "Blake": True}


def test_disablement_and_deletion_are_confirmed_states(adapter, boundary):
    boundary.state = "changed"

    disabled = adapter.verify_user(identity("Drew"))
    deleted = adapter.verify_user(identity("Emery"))

    assert isinstance(disabled, JellyfinUserSnapshot)
    assert (disabled.is_disabled, disabled.verified_at) == (True, NOW)
    assert deleted == UserDeleted(identity("Emery"), NOW)


def test_a_replaced_server_is_a_mismatch_even_when_the_user_id_exists(adapter, boundary):
    boundary.state = "replaced"

    assert adapter.verify_user(identity("Blake")) == Unverified(UnverifiedReason.SERVER_MISMATCH)
    assert adapter.verify_user(identity("Emery")) == Unverified(UnverifiedReason.SERVER_MISMATCH)
    assert adapter.item_access(identity("Blake"), [FEATURE]) == Unverified(
        UnverifiedReason.SERVER_MISMATCH
    )
    replaced = adapter.verify_user(identity("Blake", REPLACEMENT))
    assert isinstance(replaced, JellyfinUserSnapshot)
    assert replaced.identity.server_id == REPLACEMENT


@pytest.mark.parametrize("operation", ["verify", "delete", "items", "users", "authenticate"])
def test_a_server_replaced_after_a_success_is_a_mismatch(adapter, boundary, passwords, operation):
    assert isinstance(adapter.verify_user(identity("Blake")), JellyfinUserSnapshot)
    boundary.state = "replaced"
    operations = {
        "verify": lambda: adapter.verify_user(identity("Blake")),
        "delete": lambda: adapter.verify_user(identity("Emery")),
        "items": lambda: adapter.item_access(identity("Blake"), [FEATURE]),
        "users": adapter.users,
        "authenticate": lambda: adapter.authenticate("Blake", passwords[IDS["Blake"]]),
    }

    assert operations[operation]() == Unverified(UnverifiedReason.SERVER_MISMATCH)


def test_deletion_is_confirmed_only_while_the_linked_server_still_answers():
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[]) if request.url.path == "/Users" else httpx.Response(404)

    flipped = mock_adapter(answer, servers=("primary", "replacement"))
    steady = mock_adapter(answer)

    assert flipped.verify_user(identity("Emery")) == Unverified(UnverifiedReason.SERVER_MISMATCH)
    assert steady.verify_user(identity("Emery")) == UserDeleted(identity("Emery"), NOW)


def test_item_access_is_granted_only_while_the_linked_server_still_answers():
    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"Id": FEATURE, "Name": "Synthetic Feature"})

    flipped = mock_adapter(answer, servers=("primary", "replacement"))
    steady = mock_adapter(answer).item_access(identity("Blake"), [FEATURE])

    assert flipped.item_access(identity("Blake"), [FEATURE]) == Unverified(
        UnverifiedReason.SERVER_MISMATCH
    )
    assert dict(steady.decisions) == {FEATURE: ItemAccess.ALLOWED}


@pytest.mark.parametrize("operation", ["verify", "users", "authenticate", "sign-in-envelope"])
def test_a_record_naming_another_server_is_a_mismatch(operation):
    user = user_dto("Blake", REPLACEMENT if operation != "sign-in-envelope" else PRIMARY)
    result = sign_in_result(user, REPLACEMENT if operation == "sign-in-envelope" else PRIMARY)

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Users/AuthenticateByName":
            return httpx.Response(200, json=result)
        if request.url.path == "/Sessions/Logout":
            return httpx.Response(204)
        return httpx.Response(200, json=[user] if request.url.path == "/Users" else user)

    adapter = mock_adapter(answer)
    outcome = {
        "verify": lambda: adapter.verify_user(identity("Blake")),
        "users": adapter.users,
    }.get(operation, lambda: adapter.authenticate("Blake", secrets.token_urlsafe(18)))()

    assert outcome == Unverified(UnverifiedReason.SERVER_MISMATCH)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("AccessToken", MISSING, UnverifiedReason.INVALID_RESPONSE),
        ("AccessToken", "", UnverifiedReason.INVALID_RESPONSE),
        ("AccessToken", 123, UnverifiedReason.INVALID_RESPONSE),
        ("ServerId", MISSING, UnverifiedReason.INVALID_RESPONSE),
        ("User", MISSING, UnverifiedReason.INVALID_RESPONSE),
    ],
    ids=["no-token", "empty-token", "integer-token", "no-server", "no-user"],
)
def test_credential_proof_requires_a_complete_authentication_envelope(field, value, reason):
    result = sign_in_result(user_dto("Blake"))
    result = {k: v for k, v in result.items() if k != field or value is not MISSING}
    if value is not MISSING:
        result[field] = value

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Sessions/Logout":
            return httpx.Response(204)
        return httpx.Response(200, json=result)

    outcome = mock_adapter(answer).authenticate("Blake", secrets.token_urlsafe(18))

    assert outcome == Unverified(reason)


def test_server_key_visibility_never_allows_a_restricted_user(adapter, boundary):
    unknown = "c3000000000000000000000000000099"
    requested = [FEATURE, FAMILY_FILM, LATE_SHOW.upper(), MATURE, unknown, "not-a-guid"]

    casey = adapter.item_access(identity("Casey"), requested)
    blake = adapter.item_access(identity("Blake"), requested)

    assert isinstance(casey, ItemAccessSnapshot)
    assert dict(casey.decisions) == {
        FEATURE: ItemAccess.RESTRICTED,
        FAMILY_FILM: ItemAccess.ALLOWED,
        LATE_SHOW.upper(): ItemAccess.RESTRICTED,
        MATURE: ItemAccess.RESTRICTED,
        unknown: ItemAccess.NOT_FOUND,
        "not-a-guid": ItemAccess.NOT_FOUND,
    }
    assert (casey.identity, casey.verified_at) == (identity("Casey"), NOW)
    assert [blake.decisions[item] for item in requested[:4]] == [ItemAccess.ALLOWED] * 4


def test_item_access_for_a_deleted_user_allows_nothing(adapter, boundary):
    boundary.state = "changed"

    access = adapter.item_access(identity("Emery"), [FEATURE, FAMILY_FILM])

    assert dict(access.decisions) == {
        FEATURE: ItemAccess.RESTRICTED,
        FAMILY_FILM: ItemAccess.RESTRICTED,
    }


def test_media_policy_reports_library_and_parental_limits(adapter):
    casey = adapter.verify_user(identity("Casey")).media
    avery = adapter.verify_user(identity("Avery")).media

    assert casey.allows_library(LIBRARIES["Family"].upper())
    assert not casey.allows_library(LIBRARIES["Movies"])
    assert not casey.allows_library("not-a-guid")
    assert (casey.max_parental_rating, casey.blocked_tags) == (10, ("late-night",))
    assert casey.blocked_unrated == frozenset({"Movie"})
    assert avery.all_libraries and avery.allows_library(LIBRARIES["Movies"])


def test_user_list_binds_every_user_to_the_server(adapter):
    users = adapter.users()

    assert {user.identity for user in users} == {identity(name) for name in IDS}
    assert {user.verified_at for user in users} == {NOW}


def test_avatar_is_read_through_the_server(adapter):
    blake = adapter.verify_user(identity("Blake"))

    assert adapter.avatar(identity("Blake"), blake.avatar_tag) == UserAvatar(
        AVATAR_BYTES, "image/png"
    )
    assert adapter.avatar(identity("Avery"), None) is None


def test_snapshot_has_no_email_or_timezone_to_overwrite_editarr_preferences():
    template = FIXTURE["userDtoTemplate"]
    dto = user_dto("Blake")
    extended = {
        **dto,
        "Email": "someone@example.invalid",
        "TimeZone": "Pacific/Auckland",
        "Configuration": {**template["Configuration"], "TimeZone": "Pacific/Auckland"},
    }

    assert parse_user(extended, PRIMARY, NOW) == parse_user(dto, PRIMARY, NOW)
    assert {field.name for field in dataclasses.fields(JellyfinUserSnapshot)} == {
        "identity",
        "name",
        "avatar_tag",
        "is_administrator",
        "is_disabled",
        "media",
        "verified_at",
    }


def mock_adapter(answer, servers: tuple[str, ...] = ("primary",)) -> JellyfinUserAdapter:
    transport = scripted_transport(answer, servers)
    return JellyfinUserAdapter(
        "http://jellyfin.test", "key", transport=transport, clock=lambda: NOW
    )


def answering(payload: dict):
    return lambda _request: httpx.Response(200, json=payload)


def test_a_user_response_for_another_id_is_not_accepted():
    outcome = mock_adapter(answering(user_dto("Avery"))).verify_user(identity("Blake"))

    assert outcome == Unverified(UnverifiedReason.INVALID_RESPONSE)


@pytest.mark.parametrize("flag", ["IsDisabled", "IsAdministrator", "EnableAllFolders"])
def test_a_policy_without_a_required_flag_is_not_verified(flag):
    template = FIXTURE["userDtoTemplate"]
    policy = {key: value for key, value in template["Policy"].items() if key != flag}
    partial = {**user_dto("Blake"), "Policy": policy}

    outcome = mock_adapter(answering(partial)).verify_user(identity("Blake"))

    assert outcome == Unverified(UnverifiedReason.INVALID_RESPONSE)


@pytest.mark.parametrize(
    "image",
    [
        httpx.Response(200, headers={"Content-Type": "image/png"}, content=AVATAR_BYTES),
        httpx.Response(404),
    ],
    ids=["image", "absent"],
)
def test_an_avatar_read_across_a_server_replacement_is_refused(image):
    adapter = mock_adapter(lambda _request: image, ("primary", "replacement"))

    outcome = adapter.avatar(identity("Blake"), None)

    assert outcome == Unverified(UnverifiedReason.SERVER_MISMATCH)
