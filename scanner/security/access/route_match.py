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
from .routes import Gated, RouteKey
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


def interactive_only(application: Any, scope: Mapping[str, Any]) -> bool:
    """Whether every capability the matched route can exercise needs a fresh session.

    An API key can never satisfy such a route, so the middleware refuses it
    before the handler runs, whichever task later migrates that handler.
    """

    rule = ROUTE_RULES.get(matched_route(application, scope))
    return isinstance(rule, Gated) and all(
        CAPABILITIES[capability].authentication is Authentication.FRESH_INTERACTIVE
        for capability in rule.capabilities
    )
