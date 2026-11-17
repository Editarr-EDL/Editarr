"""Method-level route/action inventory against the registered application.

Run with EDITARR_DATA_DIR pointing at scratch: importing the app creates data files.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from scanner.security.access.request_access import request_access
from scanner.security.access.route_table import ROUTE_RULES
from scanner.security.access.routes import (
    ROUTE_INVENTORY_VERSION,
    AnyPrincipal,
    Gated,
    PrincipalReason,
    Public,
    PublicReason,
    inventory_gaps,
    registered_routes,
)
from scanner.security.auth_routes import (
    AUTH_JELLYFIN_LOGIN_PATH,
    AUTH_QUICK_CONNECT_PATH,
    AUTH_SESSION_PATHS,
    AUTH_SETUP_PATH,
    AUTH_STATUS_PATH,
    DOCUMENTATION_PATHS,
    PROTECTED_ROUTE_PATHS,
    SETUP_RESTORE_PATHS,
    _setup_restore_request,
)


@pytest.fixture(scope="module")
def application():
    from scanner.api import app

    return app


@pytest.fixture(scope="module")
def application_routes(application):
    return registered_routes(application.routes)


def test_every_registered_method_and_route_is_inventoried(application_routes):
    assert ROUTE_INVENTORY_VERSION == "editarr-route-actions-23"
    assert len(application_routes) == len(ROUTE_RULES) == 216
    gaps = inventory_gaps(application_routes, ROUTE_RULES)
    assert sorted(gaps.unclassified) == []
    assert sorted(gaps.stale) == []


def _route_handlers(routes, prefix=""):
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            yield from _route_handlers(
                included.routes, prefix + (getattr(context, "prefix", "") or "")
            )
        elif isinstance(route, APIRoute):
            yield route, prefix + route.path


def _dependencies(dependant):
    yield dependant.call
    for dependency in dependant.dependencies:
        yield from _dependencies(dependency)


def unresolved_gated_routes(application, rules=ROUTE_RULES):
    """Gated routes whose handler never resolves its principal through `request_access`."""

    unresolved = set()
    for route, template in _route_handlers(application.router.routes):
        resolves = request_access in set(_dependencies(route.dependant))
        for method in route.methods:
            if isinstance(rules.get((method, template)), Gated) and not resolves:
                unresolved.add((method, template))
    return unresolved


def test_every_gated_handler_resolves_its_principal(application):
    assert unresolved_gated_routes(application) == set()


def test_a_gated_route_without_a_principal_fails_the_checker(application):
    from scanner.security.access.capability_ids import Capability as C

    extended = FastAPI()
    extended.router.routes.extend(application.routes)

    @extended.post("/api/synthetic-gated")
    def synthetic_route():
        return {}

    rules = {**ROUTE_RULES, ("POST", "/api/synthetic-gated"): Gated(frozenset({C.MEDIA_BROWSE}))}
    assert unresolved_gated_routes(extended, rules) == {("POST", "/api/synthetic-gated")}


def test_sharing_context_admits_publication_checks_without_granting_publication():
    from scanner.security.access.capability_ids import Capability as C

    context = ROUTE_RULES[("GET", "/api/community/sharing/sources/{kind}/{source_id}")]
    preview = ROUTE_RULES[("POST", "/api/community/sharing/sources/{kind}/{source_id}/preview")]
    approve = ROUTE_RULES[("POST", "/api/community/sharing/previews/{preview_id}/approve")]
    assert isinstance(context, Gated)
    assert isinstance(preview, Gated)
    assert preview == approve
    checks = {C.COMMUNITY_PUBLICATIONS_CHECK_OWN, C.COMMUNITY_PUBLICATIONS_CHECK_OTHER}
    assert context.capabilities == preview.capabilities | checks
    assert not preview.capabilities & checks


def test_repair_openapi_matches_runtime_contract(application):
    document = application.openapi()
    schemas = document["components"]["schemas"]
    assert {"repair", "reason", "transient"} <= schemas["ReviewErrorDetail"]["properties"].keys()
    assert {"type": "null"} in schemas["SavedReviewSummary"]["properties"]["repair"]["anyOf"]
    assert "repair" not in schemas["SavedReviewSummary"]["required"]
    assert schemas["ScanReferences"]["properties"]["community_in_progress"]["type"] == "boolean"
    assert "community_retained" not in schemas["ScanReferences"]["properties"]
    for suffix in ("recover", "community"):
        route = document["paths"][f"/api/reviews/{{review_id}}/repair/{suffix}"]["post"]
        assert route["responses"]["409"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ReviewError"
        }
        assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/SavedReviewSummary"
        }


def test_a_new_route_or_method_without_a_rule_fails_the_checker(application):
    extended = FastAPI()
    extended.router.routes.extend(application.routes)

    @extended.post("/api/synthetic-unclassified")
    def synthetic_route():
        return {}

    @extended.delete("/api/system/version")
    def synthetic_method():
        return {}

    gaps = inventory_gaps(registered_routes(extended.routes), ROUTE_RULES)
    assert gaps.unclassified == {
        ("POST", "/api/synthetic-unclassified"),
        ("DELETE", "/api/system/version"),
    }
    assert not gaps.empty


def test_a_rule_for_a_removed_route_is_reported_stale(application_routes):
    rules = {**ROUTE_RULES, ("GET", "/api/removed"): Public(PublicReason.STATUS)}
    gaps = inventory_gaps(application_routes, rules)
    assert gaps.stale == {("GET", "/api/removed")}
    assert gaps.unclassified == frozenset()


def test_public_exceptions_are_exactly_the_auth_inventory_bootstrap_routes():
    public = {key: rule.reason for key, rule in ROUTE_RULES.items() if isinstance(rule, Public)}
    assert {key for key, reason in public.items() if reason is not PublicReason.SETUP_RESTORE} == {
        ("GET", AUTH_STATUS_PATH),
        ("POST", AUTH_SETUP_PATH),
        ("POST", AUTH_JELLYFIN_LOGIN_PATH),
        ("GET", AUTH_QUICK_CONNECT_PATH),
        ("POST", f"{AUTH_QUICK_CONNECT_PATH}/initiate"),
        ("GET", f"{AUTH_QUICK_CONNECT_PATH}/status"),
        ("POST", f"{AUTH_QUICK_CONNECT_PATH}/complete"),
        ("POST", f"{AUTH_QUICK_CONNECT_PATH}/cancel"),
    }
    restore = {key for key, reason in public.items() if reason is PublicReason.SETUP_RESTORE}
    assert {path for _, path in restore} == SETUP_RESTORE_PATHS
    for method, template in restore:
        concrete = template.replace("{job_id}", "0" * 32)
        assert _setup_restore_request(concrete, method), (method, template)


def test_documentation_is_any_principal_and_every_other_route_is_protected():
    for (method, path), rule in ROUTE_RULES.items():
        if isinstance(rule, Public):
            continue
        if path in DOCUMENTATION_PATHS:
            assert rule == AnyPrincipal(PrincipalReason.API_DOCUMENTATION), (method, path)
        else:
            assert path in PROTECTED_ROUTE_PATHS | AUTH_SESSION_PATHS, (method, path)
            assert isinstance(rule, Gated | AnyPrincipal)
    assert PROTECTED_ROUTE_PATHS <= {path for _, path in ROUTE_RULES}
