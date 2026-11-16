"""Refused operations retain the same safe identity as their successful variants."""

import asyncio

import pytest
from starlette.requests import Request

from scanner.http import account_operations, community_identity_api
from scanner.http.operations_access import Operator, operation
from scanner.http.users_access import Caller
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.catalog import CAPABILITIES
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.targets import INSTANCE
from scanner.security.audit.actions import AuditAction as A
from tests.multi_user_access_support import ADMIN, OWNER, USER, account_ref
from tests.test_audit_history_http import NOW, get
from tests.test_audit_history_http import history as history

ACCOUNT_VARIANTS = [
    (C.ACCOUNTS_JELLYFIN_IMPORT, A.ACCOUNT_CREATE, {"method": "jellyfin_import"}, INSTANCE),
    (C.ACCOUNTS_ENABLE_OTHER, A.ACCOUNT_STATUS, {"status": "active"}, account_ref(USER)),
    (C.ACCOUNTS_DISABLE_OTHER, A.ACCOUNT_STATUS, {"status": "disabled"}, account_ref(USER)),
    (C.MEDIA_ACCESS_DEFAULTS_EDIT, A.MEDIA_ACCESS_CHANGE, {"change": "default_scope"}, INSTANCE),
    (C.MEDIA_ACCESS_USER_EDIT, A.MEDIA_ACCESS_CHANGE, {"change": "user_scope"}, account_ref(USER)),
]


def assert_variant(history, action, details):
    page = get(history, OWNER, "/api/audit", action=action.value).json()
    assert page["total"] == 1
    event = page["events"][0]
    assert event["details"] == {"reason": "capability_denied", **details}
    return event["event_id"]


@pytest.mark.parametrize("boundary", ["authorize", "authorized", "locked"])
@pytest.mark.parametrize(("capability", "action", "details", "target"), ACCOUNT_VARIANTS)
def test_account_refusal_boundaries_retain_operation_variant(
    history, boundary, capability, action, details, target
):
    accounts = history[1]
    accounts.set_override(ADMIN, capability, OverrideEffect.DENY)
    with accounts.factory() as db:
        caller = Caller(db, accounts.access(ADMIN), "synthetic", NOW)
        with pytest.raises(AccessDenied):
            if boundary == "authorize":
                caller.authorize(action, capability, target)
            elif boundary == "authorized":
                account_operations.authorized(
                    caller, account_operations.Proofs(caller, None), capability, target, action
                )
            else:
                with account_operations.locked(caller, capability, target, action):
                    pytest.fail("Denied operation reached its mutation")
    identifier = assert_variant(history, action, details)
    accounts.set_override(ADMIN, capability, OverrideEffect.ALLOW)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 200


@pytest.mark.parametrize(
    ("capability", "action", "details", "paths"),
    [
        (C.PATH_MAPPINGS_CREATE, A.PATH_MAPPING_CHANGE, {"change": "created"}, True),
        (C.PATH_MAPPINGS_UPDATE, A.PATH_MAPPING_CHANGE, {"change": "updated"}, True),
        (C.PATH_MAPPINGS_DELETE, A.PATH_MAPPING_CHANGE, {"change": "deleted"}, False),
        (C.PATH_MAPPINGS_REORDER, A.PATH_MAPPING_CHANGE, {"change": "reordered"}, True),
        (C.CONNECTIONS_EDIT, A.CONNECTION_CHANGE, {"change": "saved"}, False),
        (C.CONNECTIONS_DISCONNECT, A.CONNECTION_CHANGE, {"change": "disconnected"}, False),
    ],
)
def test_operator_dependency_refusals_retain_operation_variant(
    history, capability, action, details, paths
):
    accounts = history[1]
    if CAPABILITIES[capability].delegable:
        accounts.set_override(ADMIN, capability, OverrideEffect.DENY)
    with accounts.factory() as db:
        caller = Operator(accounts.access(ADMIN), db, NOW, Request({"type": "http"}))
        with pytest.raises(AccessDenied):
            operation(capability, action, paths=paths)(caller)
    identifier = assert_variant(history, action, details)
    if CAPABILITIES[capability].delegable:
        accounts.set_override(ADMIN, capability, OverrideEffect.ALLOW)
    expected = 200 if CAPABILITIES[capability].delegable else 404
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == expected


@pytest.mark.parametrize(
    "descriptor", [item for item in community_identity_api.OPERATIONS if item.change is not None]
)
def test_identity_authorization_refusals_retain_operation_variant(history, descriptor):
    accounts = history[1]
    accounts.set_override(ADMIN, descriptor.action.own, OverrideEffect.DENY)
    with accounts.factory() as db:
        caller = community_identity_api._Caller(db, accounts.access(ADMIN))
        response = asyncio.run(community_identity_api._perform(descriptor, caller, None, None))
        assert response.status_code == 403
    identifier = assert_variant(
        history, A.COMMUNITY_IDENTITY_CHANGE, {"change": descriptor.change.value}
    )
    accounts.set_override(ADMIN, descriptor.action.own, OverrideEffect.ALLOW)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 200


@pytest.mark.parametrize("replaced", [False, True])
def test_api_key_issue_refusal_retains_create_or_rotate_variant(history, replaced):
    from scanner.http import api_key_api
    from scanner.security.api_key_crypto import load_api_key_wrap_key
    from scanner.security.api_keys import revoke_api_key

    accounts = history[1]
    with accounts.factory() as db:
        if not replaced:
            revoke_api_key(db, ADMIN)
        caller = api_key_api._Caller(
            db,
            accounts.access(ADMIN),
            "synthetic",
            NOW,
            load_api_key_wrap_key(accounts.configuration.data_dir),
        )
        with pytest.raises(AccessDenied):
            api_key_api._issue(caller, caller.own, api_key_api.FreshKeyRequest())
    page = get(history, OWNER, "/api/audit", action=A.API_KEY_ISSUE.value).json()
    assert page["total"] == 1
    event = page["events"][0]
    assert event["details"] == {"reason": "reauthentication_required", "replaced": replaced}
    assert get(history, ADMIN, f"/api/audit/{event['event_id']}").status_code == 200


def test_path_view_refusal_retains_mutation_variant(history):
    accounts = history[1]
    accounts.set_override(ADMIN, C.PATH_MAPPINGS_CREATE, OverrideEffect.ALLOW)
    accounts.set_override(ADMIN, C.MEDIA_PATHS_VIEW, OverrideEffect.DENY)
    with accounts.factory() as db:
        caller = Operator(accounts.access(ADMIN), db, NOW, Request({"type": "http"}))
        with pytest.raises(AccessDenied):
            operation(C.PATH_MAPPINGS_CREATE, A.PATH_MAPPING_CHANGE, paths=True)(caller)
    identifier = assert_variant(history, A.PATH_MAPPING_CHANGE, {"change": "created"})
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 404
    accounts.set_override(ADMIN, C.MEDIA_PATHS_VIEW, OverrideEffect.ALLOW)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 200
