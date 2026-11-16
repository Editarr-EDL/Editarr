"""Method-level route/action inventory and its fail-closed coverage checker.

Every registered FastAPI method and path must map to exactly one rule. The
anonymous static frontend is owned by the middleware and inventoried separately
by `auth_routes.PUBLIC_STATIC_ROUTE_PATHS`. There is no health route.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .capability_ids import Capability

ROUTE_INVENTORY_VERSION = "editarr-route-actions-22"

RouteKey = tuple[str, str]
"""An uppercase HTTP method and a FastAPI path template."""


class PublicReason(StrEnum):
    STATUS = "status"
    SETUP = "setup"
    SIGN_IN = "sign_in"
    SETUP_RESTORE = "setup_restore"
    """Reachable anonymously only while owner setup is still required."""


class PrincipalReason(StrEnum):
    OWN_SESSION = "own_session"
    REFERENCE_DATA = "reference_data"
    API_DOCUMENTATION = "api_documentation"


@dataclass(frozen=True)
class Public:
    reason: PublicReason


@dataclass(frozen=True)
class AnyPrincipal:
    """Any authenticated, admitted principal; no capability narrows it."""

    reason: PrincipalReason


@dataclass(frozen=True)
class Gated:
    """The capabilities a handler may exercise. It checks each one it uses.

    Own/other pairs are both listed; the handler picks by the resolved target.
    """

    capabilities: frozenset[Capability]

    def __post_init__(self) -> None:
        if not self.capabilities:
            raise ValueError("A gated route needs at least one capability")


RouteRule = Public | AnyPrincipal | Gated


@dataclass(frozen=True)
class InventoryGaps:
    unclassified: frozenset[RouteKey]
    stale: frozenset[RouteKey]

    @property
    def empty(self) -> bool:
        return not self.unclassified and not self.stale


def registered_routes(routes: Iterable[Any]) -> frozenset[RouteKey]:
    """Return every (method, path) registered on an app, including lazy nested routers."""

    def walk(collection: Iterable[Any]) -> Iterator[Any]:
        for route in collection:
            if hasattr(route, "path") and getattr(route, "methods", None) is not None:
                yield route
            elif hasattr(route, "original_router"):
                yield from walk(route.original_router.routes)
            elif hasattr(route, "router"):
                yield from walk(route.router.routes)

    return frozenset((method, route.path) for route in walk(routes) for method in route.methods)


def inventory_gaps(
    registered: Iterable[RouteKey], rules: Mapping[RouteKey, RouteRule]
) -> InventoryGaps:
    """Compare registered routes with the inventory in both directions."""

    actual = frozenset(registered)
    return InventoryGaps(
        unclassified=actual - rules.keys(),
        stale=frozenset(rules.keys()) - actual,
    )
