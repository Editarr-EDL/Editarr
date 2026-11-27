"""Match a request to its registered route template and route-table rule.

Starlette tries routes in registration order, so the first route matching the
method and path is the one that runs. `include_router` adds a lazy branch
without a `path`; descend into it with its prefix so router-mounted handlers
match by their full template.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from functools import lru_cache
from re import Pattern
from typing import Any

from starlette._utils import get_route_path
from starlette.routing import compile_path

from .catalog import CAPABILITIES
from .route_table import ROUTE_RULES
from .routes import AnyPrincipal, Gated, PrincipalReason, RouteKey
from .vocabulary import Authentication


@lru_cache(maxsize=1024)
def _template_pattern(template: str) -> Pattern[str]:
    return compile_path(template)[0]


def _route_templates(routes: Any, prefix: str = "") -> Iterator[tuple[Any, str]]:
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            yield from _route_templates(
                included.routes, prefix + (getattr(context, "prefix", "") or "")
            )
        elif isinstance(getattr(route, "path", None), str):
            yield route, prefix + route.path


def matched_route(application: Any, scope: Mapping[str, Any]) -> RouteKey | None:
    """The method and full template of the route that will handle this request."""

    path = get_route_path(scope)
    method = scope["method"]
    for route, template in _route_templates(application.router.routes):
        methods = getattr(route, "methods", None)
        if _template_pattern(template).match(path) and (methods is None or method in methods):
            return method, template
    return None


def route_interactive_only(key: RouteKey | None) -> bool:
    """Whether every capability the route `key` can exercise needs a fresh session.

    Shared by the API-key middleware and the published OpenAPI security.
    """

    rule = ROUTE_RULES.get(key) if key is not None else None
    return isinstance(rule, Gated) and all(
        CAPABILITIES[capability].authentication is Authentication.FRESH_INTERACTIVE
        for capability in rule.capabilities
    )


def route_session_cookie_only(key: RouteKey | None) -> bool:
    """Whether the route `key` acts on the caller's own browser session.

    Such handlers read the session cookie itself, so an API key is admitted by
    the middleware but finds no session to act on.
    """

    rule = ROUTE_RULES.get(key) if key is not None else None
    return isinstance(rule, AnyPrincipal) and rule.reason is PrincipalReason.OWN_SESSION


def interactive_only(application: Any, scope: Mapping[str, Any]) -> bool:
    """Whether the matched route needs a fresh session for every capability.

    An API key can never satisfy such a route, so the middleware refuses it
    before the handler runs, whichever task later migrates that handler.
    """

    return route_interactive_only(matched_route(application, scope))
