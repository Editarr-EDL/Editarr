"""MU-04: the evaluator across roles, overrides, targets, credentials, and account states."""

from dataclasses import replace

import pytest

from scanner.security.access.api_shapes import AccessErrorCode as E
from scanner.security.access.authorization import capability_payload, may_attempt
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.catalog import CAPABILITIES, DELEGABLE
from scanner.security.access.decisions import Allowed, Denied
from scanner.security.access.evaluator import decide
from scanner.security.access.fresh import FreshAuthentication
from scanner.security.access.identity import AccountStatus, CredentialKind
from scanner.security.access.overrides import OverrideEffect as O
from scanner.security.access.targets import (
    ActionPair,
    MediaRef,
    TargetMismatch,
)
from scanner.security.access.vocabulary import TARGET_RULES, Authentication, Role
from scanner.security.access.vocabulary import Target as Kind
from tests.multi_user_access_support import (
    ADMIN,
    ALLOWED_MEDIA,
    DENIED_MEDIA,
    OWNER,
    PEER_ADMIN,
    PEER_USER,
    USER,
    account_ref,
    build_access,
    minted_proof,
    resource_of,
    self_ref,
    valid_target,
)
from tests.test_multi_user_capabilities import MATRIX, ROLE_COLUMNS, _grants

API = CredentialKind.API_KEY
SESSION = CredentialKind.SESSION
OWNER_ONLY = sorted(c for c, row in CAPABILITIES.items() if row.owner_power is not None)
OTHERS = {Kind.OTHER_ACCOUNT, Kind.OTHER_RESOURCE, Kind.SCOPED_RECORDS}


def _code(decision):
    return decision.code if isinstance(decision, Denied) else "allowed"


@pytest.mark.parametrize("capability", sorted(CAPABILITIES), ids=str)
def test_default_grants_agree_with_every_cited_matrix_cell(capability):
    row = CAPABILITIES[capability]
    facet = TARGET_RULES[row.target].facet
    for action in row.matrix_rows:
        cells = dict(zip(ROLE_COLUMNS, MATRIX[action], strict=True))
        for role in (Role.USER, Role.ADMIN):
            held = capability in build_access(role).capabilities
            assert held is _grants(cells[role], facet), (action, role)
    assert capability in build_access(Role.OWNER).capabilities


def _expected(role, credential, capability):
    """The confirmed rules for an active account and a valid target.

    Self and own-resource targets belong to the actor. Other-account targets
    belong to an ordinary user.
    Without the capability, another account's target is hidden (`not_found`)
    unless the actor can list accounts; resources are hidden by default. Fresh
    actions refuse API keys for every role.
    """

    spec = CAPABILITIES[capability]
    if role is not Role.OWNER and role not in spec.granted:
        hidden = spec.target in OTHERS and not (
            spec.target is Kind.OTHER_ACCOUNT and role is Role.ADMIN
        )
        return E.NOT_FOUND if hidden else E.CAPABILITY_DENIED
    if spec.authentication is Authentication.FRESH_INTERACTIVE and credential is API:
        return E.INTERACTIVE_SESSION_REQUIRED
    return "allowed"


@pytest.mark.parametrize("capability", sorted(CAPABILITIES), ids=str)
def test_every_capability_by_role_credential_and_account_state(capability):
    for role in Role:
        for credential in (SESSION, API):
            access = build_access(role, credential=credential)
            proof = minted_proof(access) if credential is SESSION else None
            decision = decide(access, capability, valid_target(capability, access), fresh=proof)
            assert _code(decision) == _expected(role, credential, capability), (role, credential)
            if role is not Role.OWNER:
                disabled = build_access(role, credential=credential, status=AccountStatus.DISABLED)
                target = valid_target(capability, disabled)
                assert _code(decide(disabled, capability, target, fresh=proof)) == (
                    E.ACCOUNT_DISABLED
                )


@pytest.mark.parametrize(
    ("role", "capability", "default", "override", "held"),
    [
        (Role.USER, C.MODELS_DOWNLOAD, None, None, True),
        (Role.USER, C.MODELS_DOWNLOAD, False, None, False),
        (Role.USER, C.MODELS_DOWNLOAD, False, O.ALLOW, True),
        (Role.USER, C.MODELS_DOWNLOAD, True, O.DENY, False),
        (Role.USER, C.MODELS_DOWNLOAD, None, O.DENY, False),
        (Role.USER, C.MODELS_DELETE, None, None, False),
        (Role.USER, C.MODELS_DELETE, True, None, True),
        (Role.USER, C.MODELS_DELETE, None, O.ALLOW, True),
        (Role.ADMIN, C.MODELS_DELETE, None, O.DENY, False),
        (Role.ADMIN, C.MODELS_DELETE, False, None, False),
        (Role.ADMIN, C.BACKUPS_DELETE, None, None, False),
        (Role.ADMIN, C.BACKUPS_DELETE, None, O.ALLOW, True),
        (Role.ADMIN, C.ACCOUNTS_ADMINS_MANAGE, True, None, True),
    ],
)
def test_role_defaults_and_individual_overrides_combine(role, capability, default, override, held):
    access = build_access(
        role,
        role_defaults={} if default is None else {capability: default},
        overrides={} if override is None else {capability: override},
    )
    assert (capability in access.capabilities) is held


@pytest.mark.parametrize("override", [None, O.ALLOW])
def test_an_unreadable_stored_row_denies_its_capability(override):
    access = build_access(
        Role.ADMIN,
        overrides={} if override is None else {C.LOGS_VIEW: override},
        unreadable={C.LOGS_VIEW},
    )
    assert C.LOGS_VIEW not in access.capabilities
    assert C.LOGS_SETTINGS_EDIT in access.capabilities


@pytest.mark.parametrize("effect", [O.ALLOW, O.DENY])
def test_role_fixed_capabilities_ignore_every_stored_grant(effect):
    stored = {
        "role_defaults": {c: effect is O.ALLOW for c in C},
        "overrides": {c: effect for c in C},
    }
    assert C.ARCHIVE_VIEW in build_access(Role.ADMIN, **stored).capabilities
    assert C.ARCHIVE_VIEW not in build_access(Role.USER, **stored).capabilities


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN])
@pytest.mark.parametrize("capability", OWNER_ONLY, ids=str)
def test_owner_powers_never_reach_another_role_even_from_corrupt_grants(role, capability):
    access = build_access(
        role,
        role_defaults={c: True for c in C},
        overrides={c: O.ALLOW for c in C},
    )
    assert capability not in access.capabilities
    decision = decide(
        access, capability, valid_target(capability, access), fresh=minted_proof(access)
    )
    assert not decision.allowed


def test_the_owner_keeps_everything_whatever_is_stored():
    access = build_access(
        Role.OWNER,
        role_defaults={c: False for c in C},
        overrides={c: O.DENY for c in DELEGABLE},
        unreadable=DELEGABLE,
    )
    assert access.capabilities == frozenset(C)
    review = resource_of(USER, DENIED_MEDIA)
    assert decide(access, C.REVIEWS_DELETE_OTHER, review) == Allowed(C.REVIEWS_DELETE_OTHER)
    assert decide(access, C.MEDIA_BROWSE, MediaRef(DENIED_MEDIA)).allowed


DISABLE = C.ACCOUNTS_DISABLE_OTHER


@pytest.mark.parametrize(
    ("actor", "overrides", "target", "result"),
    [
        (Role.OWNER, {}, PEER_ADMIN, "allowed"),
        (Role.OWNER, {}, USER, "allowed"),
        (Role.ADMIN, {}, USER, "allowed"),
        (Role.ADMIN, {}, PEER_ADMIN, E.PROTECTED_ACCOUNT),
        (Role.ADMIN, {}, OWNER, E.PROTECTED_ACCOUNT),
        (Role.ADMIN, {C.ACCOUNTS_ADMINS_MANAGE: O.ALLOW}, PEER_ADMIN, "allowed"),
        (Role.ADMIN, {C.ACCOUNTS_ADMINS_MANAGE: O.ALLOW}, OWNER, E.PROTECTED_ACCOUNT),
        (Role.ADMIN, {DISABLE: O.DENY}, USER, E.CAPABILITY_DENIED),
        (Role.ADMIN, {C.ACCOUNTS_LIST: O.DENY, DISABLE: O.DENY}, USER, E.NOT_FOUND),
        (Role.USER, {}, PEER_USER, E.NOT_FOUND),
        (Role.USER, {}, PEER_ADMIN, E.NOT_FOUND),
        (Role.USER, {}, OWNER, E.NOT_FOUND),
        (Role.USER, {DISABLE: O.ALLOW}, PEER_USER, "allowed"),
        (Role.USER, {DISABLE: O.ALLOW}, PEER_ADMIN, E.NOT_FOUND),
        (Role.USER, {DISABLE: O.ALLOW, C.ACCOUNTS_LIST: O.ALLOW}, PEER_ADMIN, E.PROTECTED_ACCOUNT),
        (Role.USER, {DISABLE: O.ALLOW, C.ACCOUNTS_LIST: O.ALLOW}, OWNER, E.PROTECTED_ACCOUNT),
    ],
)
def test_account_targets_protect_admins_and_owner_and_hide_from_users(
    actor, overrides, target, result
):
    access = build_access(actor, overrides=overrides)
    assert _code(decide(access, DISABLE, account_ref(target))) == result


def test_users_cannot_list_accounts_and_admins_cannot_assign_roles():
    user, admin = build_access(Role.USER), build_access(Role.ADMIN)
    assert _code(decide(user, C.ACCOUNTS_LIST)) == E.CAPABILITY_DENIED
    assert decide(admin, C.ACCOUNTS_LIST).allowed
    assert _code(decide(admin, C.ROLES_ASSIGN, account_ref(USER), fresh=minted_proof(admin))) == (
        E.CAPABILITY_DENIED
    )


DELETE_REVIEW = ActionPair(C.REVIEWS_DELETE_OWN, C.REVIEWS_DELETE_OTHER)


@pytest.mark.parametrize(
    ("actor", "overrides", "action", "owner", "media", "visible", "result"),
    [
        (Role.USER, {}, DELETE_REVIEW, USER, ALLOWED_MEDIA, False, C.REVIEWS_DELETE_OWN),
        (Role.USER, {}, DELETE_REVIEW, PEER_USER, ALLOWED_MEDIA, False, E.NOT_FOUND),
        (Role.USER, {}, DELETE_REVIEW, PEER_USER, ALLOWED_MEDIA, True, E.CAPABILITY_DENIED),
        (
            Role.USER,
            {C.REVIEWS_DELETE_OWN: O.DENY},
            DELETE_REVIEW,
            USER,
            ALLOWED_MEDIA,
            False,
            E.CAPABILITY_DENIED,
        ),
        (Role.USER, {}, DELETE_REVIEW, USER, DENIED_MEDIA, False, E.NOT_FOUND),
        (Role.USER, {}, DELETE_REVIEW, USER, DENIED_MEDIA, True, E.MEDIA_UNAVAILABLE),
        (Role.ADMIN, {}, DELETE_REVIEW, USER, ALLOWED_MEDIA, False, C.REVIEWS_DELETE_OTHER),
        (Role.ADMIN, {}, DELETE_REVIEW, OWNER, ALLOWED_MEDIA, False, C.REVIEWS_DELETE_OTHER),
        (Role.ADMIN, {}, DELETE_REVIEW, USER, DENIED_MEDIA, False, E.NOT_FOUND),
        (Role.OWNER, {}, DELETE_REVIEW, USER, DENIED_MEDIA, False, C.REVIEWS_DELETE_OTHER),
    ],
)
def test_own_and_other_resources_by_current_owner_media_and_visibility(
    actor, overrides, action, owner, media, visible, result
):
    access = build_access(actor, overrides=overrides)
    decision = decide(access, action, resource_of(owner, media, visible=visible))
    expected = Allowed(result) if isinstance(result, C) else Denied(result)
    assert decision == expected


def _forged(access):
    return FreshAuthentication(access, object())


@pytest.mark.parametrize(
    ("role", "capability"),
    [
        (Role.OWNER, C.OWNERSHIP_TRANSFER),
        (Role.ADMIN, C.ACCOUNTS_LINK_OWN),
        (Role.ADMIN, C.ACCOUNTS_DELETE_OTHER),
        (Role.USER, C.API_KEYS_ROTATE_OWN),
    ],
)
def test_fresh_actions_need_this_requests_genuine_proof_and_never_an_api_key(role, capability):
    session = build_access(role)
    other_user = build_access(Role.USER, user_id=PEER_USER)
    assert replace(session) == session
    target = (
        account_ref(PEER_USER)
        if capability is C.OWNERSHIP_TRANSFER
        else valid_target(capability, session)
    )
    cases = {
        None: E.REAUTHENTICATION_REQUIRED,
        "genuine": "allowed",
        "forged": E.REAUTHENTICATION_FAILED,
        "other_request": E.REAUTHENTICATION_FAILED,
        "other_session": E.REAUTHENTICATION_FAILED,
        "other_user": E.REAUTHENTICATION_FAILED,
    }
    proofs = {
        None: None,
        "genuine": minted_proof(session),
        "forged": _forged(session),
        "other_request": minted_proof(replace(session)),
        "other_session": minted_proof(build_access(role, session_id=session.session_id + 1)),
        "other_user": minted_proof(other_user),
    }
    for name, expected in cases.items():
        assert _code(decide(session, capability, target, fresh=proofs[name])) == expected, name
    api = build_access(role, credential=API)
    for proof in proofs.values():
        assert _code(decide(api, capability, target, fresh=proof)) == E.INTERACTIVE_SESSION_REQUIRED


def test_ordinary_users_cannot_link_even_with_a_genuine_proof():
    user = build_access(Role.USER)
    assert _code(decide(user, C.ACCOUNTS_LINK_OWN, self_ref(user), fresh=minted_proof(user))) == (
        E.CAPABILITY_DENIED
    )


def test_a_target_the_capability_cannot_act_on_is_a_caller_defect():
    user = build_access(Role.USER)
    with pytest.raises(TargetMismatch):
        decide(user, C.REVIEWS_DELETE_OTHER, resource_of(USER, ALLOWED_MEDIA))
    with pytest.raises(TargetMismatch):
        decide(user, C.REVIEWS_DELETE_OWN, resource_of(PEER_USER, ALLOWED_MEDIA))
    with pytest.raises(TargetMismatch):
        decide(user, C.MODELS_DOWNLOAD, account_ref(USER))
    with pytest.raises(TargetMismatch):
        decide(user, C.PROFILE_VIEW_OWN, account_ref(PEER_USER))
    with pytest.raises(ValueError):
        ActionPair(C.REVIEWS_DELETE_OTHER, C.REVIEWS_DELETE_OWN)


@pytest.mark.parametrize(
    ("capability", "results"),
    [
        (C.MODELS_DOWNLOAD, ("allowed", "allowed", "allowed")),
        (C.MODELS_PROGRESS_VIEW_OWN, ("allowed", "allowed", "allowed")),
        (C.MODELS_INVENTORY_VIEW, (E.CAPABILITY_DENIED, "allowed", "allowed")),
        (C.MODELS_DOWNLOAD_CANCEL, (E.CAPABILITY_DENIED, "allowed", "allowed")),
        (C.MODELS_DELETE, (E.CAPABILITY_DENIED, "allowed", "allowed")),
        (C.BACKUPS_CREATE, (E.CAPABILITY_DENIED, "allowed", "allowed")),
        (C.BACKUPS_SCHEDULE_EDIT, (E.CAPABILITY_DENIED, "allowed", "allowed")),
        (C.BACKUPS_STATUS_VIEW, (E.CAPABILITY_DENIED, "allowed", "allowed")),
        (C.BACKUPS_DOWNLOAD, (E.CAPABILITY_DENIED, E.CAPABILITY_DENIED, "allowed")),
        (C.BACKUPS_DELETE, (E.CAPABILITY_DENIED, E.CAPABILITY_DENIED, "allowed")),
    ],
)
def test_model_and_backup_rows_by_default(capability, results):
    for role, expected in zip((Role.USER, Role.ADMIN, Role.OWNER), results, strict=True):
        access = build_access(role)
        target = valid_target(capability, access)
        assert _code(decide(access, capability, target, fresh=minted_proof(access))) == expected


def test_capability_payload_lists_only_what_this_principal_may_attempt():
    session = build_access(Role.USER, revision=7)
    payload = capability_payload(session)
    assert payload.version == "editarr-capabilities-10"
    assert payload.permission_revision == 7
    assert payload.principal.user_id == USER and payload.principal.credential is SESSION
    assert C.API_KEYS_ROTATE_OWN in payload.capabilities
    assert not set(payload.capabilities) & set(OWNER_ONLY)
    assert C.ACCOUNTS_LIST not in payload.capabilities
    keyed = capability_payload(build_access(Role.OWNER, credential=API))
    fresh = {c for c, row in CAPABILITIES.items() if row.authentication is not Authentication.ANY}
    assert set(keyed.capabilities) == set(C) - fresh
    assert len(capability_payload(build_access(Role.OWNER)).capabilities) == len(C)
    disabled = build_access(Role.ADMIN, status=AccountStatus.DISABLED)
    assert capability_payload(disabled).capabilities == []


def test_serializer_hints_show_fresh_actions_that_a_password_would_allow():
    user = build_access(Role.USER)
    assert may_attempt(user, C.API_KEYS_REVEAL_OWN, self_ref(user))
    assert not may_attempt(
        build_access(Role.USER, credential=API), C.API_KEYS_REVEAL_OWN, self_ref(user)
    )
    assert not may_attempt(user, C.MODELS_DELETE)
    assert may_attempt(build_access(Role.ADMIN), C.MODELS_DELETE)
    assert not may_attempt(user, DELETE_REVIEW, resource_of(ADMIN, ALLOWED_MEDIA))
