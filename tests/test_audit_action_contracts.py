"""Compare audit visibility with the declarations and checks used by producers."""

import ast
import inspect
import textwrap

import pytest

from scanner import api
from scanner.http import (
    account_operations,
    api_system,
    backup_api,
    backup_schedule_api,
    catalog_settings_api,
    community_identity_api,
    deployment_api,
    filter_policy_api,
    jellyfin_accounts_api,
    media_access_api,
    restart_api,
    scan_catalog_api,
)
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import AccountStatus
from scanner.security.access.route_table import ROUTE_RULES
from scanner.security.access.routes import registered_routes
from scanner.security.access.vocabulary import Role
from scanner.security.audit.actions import ACTION_SPECS
from scanner.security.audit.actions import AuditAction as A
from scanner.security.audit.visibility import ACTION_CAPABILITIES, ByAccount, ByDetail


def requirements(action, details=None, *, own=False):
    requirement = ACTION_CAPABILITIES[action]
    if isinstance(requirement, ByDetail):
        requirement = requirement.choices[(details or {})[requirement.key]]
    if isinstance(requirement, ByAccount):
        requirement = requirement.own if own else requirement.other
    if requirement is None:
        return None
    return frozenset({requirement}) if isinstance(requirement, C) else requirement


DEPENDENCY_VARIANTS = {
    C.PATH_MAPPINGS_CREATE: {"change": "created"},
    C.PATH_MAPPINGS_UPDATE: {"change": "updated"},
    C.PATH_MAPPINGS_DELETE: {"change": "deleted"},
    C.PATH_MAPPINGS_REORDER: {"change": "reordered"},
    C.CONNECTIONS_EDIT: {"change": "saved"},
    C.CONNECTIONS_DISCONNECT: {"change": "disconnected"},
}


def audited_dependencies():
    modules = (
        api_system,
        backup_api,
        backup_schedule_api,
        catalog_settings_api,
        deployment_api,
        restart_api,
        scan_catalog_api,
    )
    for module in modules:
        for route in module.router.routes:
            for dependency in route.dependant.dependencies:
                if not inspect.isfunction(dependency.call):
                    continue
                declared = inspect.getclosurevars(dependency.call).nonlocals
                if declared.get("audited") is not None and "capability" in declared:
                    yield pytest.param(route, declared, id=f"{sorted(route.methods)}:{route.path}")


@pytest.mark.parametrize(("route", "declared"), list(audited_dependencies()))
def test_operator_route_declarations_match_visibility_requirements(route, declared):
    capability = declared["capability"]
    expected = {capability}
    if declared["paths"]:
        expected.add(C.MEDIA_PATHS_VIEW)
    assert requirements(declared["audited"], DEPENDENCY_VARIANTS.get(capability)) == expected
    for method in route.methods:
        assert capability in ROUTE_RULES[(method, route.path)].capabilities
        assert (method, route.path) in registered_routes(api.app.routes)


@pytest.mark.parametrize(
    "descriptor", [item for item in community_identity_api.OPERATIONS if item.change is not None]
)
def test_identity_operations_match_both_visibility_branches_and_inventory(descriptor):
    details = {"change": descriptor.change.value}
    assert requirements(A.COMMUNITY_IDENTITY_CHANGE, details, own=True) == {descriptor.action.own}
    assert requirements(A.COMMUNITY_IDENTITY_CHANGE, details) == {descriptor.action.other}
    for prefix in (
        community_identity_api.router.prefix,
        community_identity_api.users_router.prefix,
    ):
        declared = ROUTE_RULES[(descriptor.method, prefix + descriptor.path)].capabilities
        assert {descriptor.action.own, descriptor.action.other} <= declared


@pytest.mark.parametrize(
    ("descriptor", "details"),
    [
        (account_operations.status(AccountStatus.ACTIVE), {"status": "active"}),
        (account_operations.status(AccountStatus.DISABLED), {"status": "disabled"}),
        (account_operations.REVOKE_SESSIONS, {}),
        (account_operations.role(Role.USER), {"change": "role"}),
        (account_operations.media_scope(None), {"change": "user_scope"}),
    ],
)
def test_managed_and_bulk_operations_match_visibility(descriptor, details):
    assert requirements(descriptor.audited, details) == {descriptor.capability}


def literal_checks(function):
    """Extract literal capability arguments, not unrelated references or response checks."""
    result = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(function)))):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", None))
        if name not in {"require", "authorize", "authorized", "decide"}:
            continue
        for argument in node.args:
            if isinstance(argument, ast.Attribute) and isinstance(argument.value, ast.Name):
                if argument.value.id in {"C", "Capability"}:
                    result.add(C[argument.attr])
    return result


@pytest.mark.parametrize(
    ("action", "details", "function"),
    [
        (
            A.ACCOUNT_CREATE,
            {"method": "jellyfin_import"},
            jellyfin_accounts_api.import_jellyfin_users,
        ),
        (A.JELLYFIN_SYNC, {}, jellyfin_accounts_api.sync_jellyfin_accounts),
        (A.MEDIA_ACCESS_CHANGE, {"change": "default_scope"}, media_access_api.change_default),
    ],
)
def test_literal_producer_authorization_matches_visibility(action, details, function):
    assert requirements(action, details) == literal_checks(function)


@pytest.mark.parametrize(
    ("action", "details", "own", "capability"),
    [
        (A.ACCOUNT_CHANGE, {"changed": "password"}, True, C.PROFILE_EDIT_OWN),
        (A.ACCOUNT_CHANGE, {"changed": "password_and_username"}, True, C.PROFILE_EDIT_OWN),
        (A.ACCOUNT_CREATE, {"method": "managed"}, False, C.ACCOUNTS_JELLYFIN_IMPORT),
        (A.ACCOUNT_PASSWORD_RESET, {}, False, C.ACCOUNTS_SESSIONS_REVOKE_OTHER),
        (A.REGISTRATION_CHANGE, {}, False, C.CONNECTIONS_EDIT),
        (A.JELLYFIN_ADMISSION_CHANGE, {}, False, C.CONNECTIONS_EDIT),
    ],
)
def test_history_of_removed_local_password_actions_stays_readable(action, details, own, capability):
    assert requirements(action, details, own=own) == {capability}


def test_instance_defaults_visibility_matches_both_actual_authorization_layers():
    expected = literal_checks(catalog_settings_api.api_update_filter_defaults_settings)
    expected |= literal_checks(filter_policy_api._reauthenticate)
    assert expected == {C.FILTERS_INSTANCE_DEFAULTS_EDIT, C.FILTERS_POLICY_ENFORCE}
    assert requirements(A.FILTER_POLICY_CHANGE, {"cause": "instance_defaults"}) == expected


def test_every_registered_action_has_a_reviewed_visibility_rule():
    assert len(ACTION_SPECS) == 42
    assert set(ACTION_CAPABILITIES) == set(ACTION_SPECS) == set(A)
