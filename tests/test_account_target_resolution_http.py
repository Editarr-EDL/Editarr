"""Routes that name an account by ID hide whether it exists from callers who may not know.

A caller without `accounts.list` or the route's other-account capability gets one
answer for an ordinary, protected, or disabled account and for an ID that names
nothing. Callers who may see the directory still learn the difference.
"""

import pytest

import scanner.http.filter_policy_api as filter_policy_api
from scanner.api import app as production
from scanner.http import api_key_api
from scanner.http import community_identity_api as identity
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.routes import registered_routes
from tests.multi_user_access_support import ADMIN, OWNER, PEER_ADMIN, PEER_USER, USER
from tests.users_api_http_support import accounts as accounts
from tests.users_api_http_support import code, fresh, png
from tests.users_api_http_support import users_app as users_app

MISSING = 987654321
USERS = "/api/users/{user_id}"
FILTERS = "/api/settings/filters/users/{user_id}"
PROOF = fresh(USER)
REQUESTS = {
    ("GET", USERS): {},
    ("PATCH", USERS): {"json": {"language": "en"}},
    ("GET", USERS + "/avatar"): {},
    ("PUT", USERS + "/avatar"): {"content": png(), "headers": {"Content-Type": "image/png"}},
    ("DELETE", USERS + "/avatar"): {},
    ("PUT", USERS + "/status"): {"json": {"status": "disabled"}},
    ("DELETE", USERS + "/sessions"): {},
    ("PUT", USERS + "/role"): {"json": {"role": "admin", **PROOF}},
    ("POST", USERS + "/ownership"): {"json": PROOF},
    ("POST", USERS + "/link/proof"): {
        "json": {"jellyfin": {"username": "probe", "password": "probe"}, **PROOF}
    },
    ("POST", USERS + "/link"): {"json": {"proof": "probe", "confirmation": "0" * 64, **PROOF}},
    ("GET", USERS + "/deletion"): {"params": {"mode": "retain"}},
    ("POST", USERS + "/deletion"): {"json": {"mode": "retain", "confirmation": "0" * 64}},
    ("GET", USERS + "/permissions"): {},
    ("PATCH", USERS + "/permissions"): {"json": {"overrides": {"scans.start": "deny"}, **PROOF}},
    ("GET", USERS + "/media-access"): {},
    ("PUT", USERS + "/media-access"): {"json": {"all_sources": True, **PROOF}},
    ("POST", USERS + "/api-key"): {"json": PROOF},
    ("POST", USERS + "/api-key/reveal"): {"json": PROOF},
    ("DELETE", USERS + "/api-key"): {},
    ("GET", FILTERS): {},
    ("PUT", FILTERS): {"json": {"mode": "live", "revision": 0}},
    ("PUT", FILTERS + "/mode"): {"json": {"mode": "copy", "revision": 0}},
    ("PUT", FILTERS + "/policy"): {"json": {"policy_revision": 0, **PROOF}},
}
COMMUNITY_BODIES = {
    None: {},
    identity.EnrollmentConfirmation: {"json": {"state_id": "0" * 64, "confirmed": True}},
    identity.ProfileUpdate: {"json": {"display_name": "Probe", "operation_id": "probe"}},
    identity.RotationConfirmation: {"json": {"confirmed": True}},
    identity.RecoveryStart: {"json": {"confirmed": True, "old_key_id": "sha256:" + "0" * 64}},
}
REQUESTS |= {
    (operation.method, identity.users_router.prefix + operation.path): COMMUNITY_BODIES[
        operation.body
    ]
    for operation in identity.OPERATIONS
}


@pytest.fixture
def app(users_app):
    users_app.client.app.include_router(filter_policy_api.router)
    users_app.client.app.include_router(identity.users_router)
    users_app.client.app.include_router(api_key_api.router)
    return users_app


def _send(app, route, actor, target):
    method, template = route
    return app.send(method, template.format(user_id=target), actor, **REQUESTS[route])


def test_every_route_taking_an_account_id_is_probed():
    routed = {key for key in registered_routes(production.routes) if "{user_id}" in key[1]}
    assert routed == set(REQUESTS)


@pytest.mark.parametrize("route", sorted(REQUESTS), ids=" ".join)
def test_hidden_and_missing_accounts_answer_alike(app, route):
    app.ok("PUT", f"/api/users/{PEER_ADMIN}/status", OWNER, {"status": "disabled"})
    answers = {}
    for target in (PEER_USER, ADMIN, OWNER, PEER_ADMIN, MISSING):
        response = _send(app, route, USER, target)
        answers[target] = (response.status_code, response.content)
    assert len(set(answers.values())) == 1, answers
    assert code(response) in {(403, "capability_denied"), (404, "not_found")}


def test_a_refused_change_to_an_existing_hidden_account_is_audited(app):
    route = ("PUT", USERS + "/status")
    _send(app, route, USER, PEER_USER)
    _send(app, route, USER, MISSING)
    refusals = [row for row in app.audit() if row[0] == "account.status"]
    assert refusals == [
        ("account.status", "denied", USER, PEER_USER, '{"reason":"not_found","status":"disabled"}')
    ]


def test_directory_readers_learn_which_ids_exist(app):
    route = ("GET", USERS + "/permissions")
    assert code(_send(app, route, ADMIN, PEER_USER)) == (403, "capability_denied")
    assert code(_send(app, route, ADMIN, MISSING)) == (404, "not_found")


def test_a_delegated_capability_still_reaches_its_targets(app):
    app.accounts.set_override(USER, C.ACCOUNTS_DISABLE_OTHER, OverrideEffect.ALLOW)
    route = ("PUT", USERS + "/status")
    assert _send(app, route, USER, PEER_USER).status_code == 200
    assert code(_send(app, route, USER, ADMIN)) == (404, "not_found")
    assert code(_send(app, route, USER, MISSING)) == (404, "not_found")
