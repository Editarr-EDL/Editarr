"""The response of local routes whose revision protocol is retired (ADR 0002, D2)."""

from starlette.responses import JSONResponse

from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.grants import PrincipalAccess


def protocol_retired(access: PrincipalAccess | None = None, capability: Capability | None = None):
    """`410 {"error": "protocol_retired"}` for a route the master-set API replaces.

    With `access` and `capability`, the route's registered gate still applies first:
    a caller without `capability` gets `403 capability_denied`.
    """
    if access is not None and capability not in access.capabilities:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    return JSONResponse(
        {"error": "protocol_retired"}, status_code=410, headers={"Cache-Control": "no-store"}
    )
