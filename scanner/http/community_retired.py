"""The response of local routes whose revision protocol is retired (ADR 0002, D2)."""

from starlette.responses import JSONResponse


def protocol_retired() -> JSONResponse:
    """`410 {"error": "protocol_retired"}` for a route the master-set API replaces."""
    return JSONResponse(
        {"error": "protocol_retired"}, status_code=410, headers={"Cache-Control": "no-store"}
    )
