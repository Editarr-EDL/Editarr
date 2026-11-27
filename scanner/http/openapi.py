"""Document middleware authentication, curated tags, and application-prefix servers."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from scanner.security.access.route_match import (
    route_interactive_only,
    route_session_cookie_only,
)
from scanner.security.auth_routes import (
    AUTH_JELLYFIN_LOGIN_PATH,
    AUTH_QUICK_CONNECT_PATHS,
    AUTH_SETUP_PATH,
    AUTH_STATUS_PATH,
    SETUP_RESTORE_PATHS,
)

from .openapi_tags import TAGS, tag_for

DESCRIPTION = (
    "Local Editarr API. Send a personal key in the X-Api-Key header; a key carries the "
    "signed-in account's own current permissions, never more. Browser-session mutations "
    "must be same-origin and include the X-CSRF-Token header. Some account-security "
    "operations require an interactive browser session and return 403 "
    "interactive_session_required when called with an API key."
)
BROWSER_ONLY_NOTES = {
    "interactive": (
        "Requires an interactive browser session; API keys receive 403 "
        "interactive_session_required."
    ),
    "session_cookie": (
        "Acts on the caller's own browser session and reads only its session cookie; "
        "a request with just an API key receives 401 authentication_required."
    ),
    "setup_restore": (
        "Part of the first-run browser restore flow, available only before an administrator "
        "exists. Requests must be same-origin (Origin or Referer), carry the "
        "editarr_setup_restore cookie from POST /api/setup-restore/flow, and send that "
        "response's token in X-Setup-Restore-Token on every change."
    ),
}
"""Browser-only access kinds, published as `x-editarr-browser-session`, and their notes."""


def _browser_only(method: str, path: str) -> str | None:
    """Which browser-only kind the route policy gives an operation, if any."""

    key = (method.upper(), path)
    if route_interactive_only(key):
        return "interactive"
    if route_session_cookie_only(key):
        return "session_cookie"
    return None


def _document_access(operation: dict, public: bool, method: str, path: str) -> None:
    """Publish an operation's credentials and, for browser-only kinds, a marker and note."""

    if path in SETUP_RESTORE_PATHS:
        browser_only = "setup_restore"
    else:
        browser_only = None if public else _browser_only(method, path)
    if public:
        operation["security"] = []
    elif browser_only is None:
        operation["security"] = [{"ApiKey": []}, {"BrowserSession": []}]
    else:
        operation["security"] = [{"BrowserSession": []}]
    if browser_only is not None:
        operation["x-editarr-browser-session"] = browser_only
        note = BROWSER_ONLY_NOTES[browser_only]
        description = operation.get("description")
        operation["description"] = f"{description}\n\n{note}" if description else note


def configure_api_documentation(application: FastAPI) -> None:
    """Expose X-Api-Key in Swagger without replacing the global access policy."""

    def schema():
        if application.openapi_schema is None:
            document = get_openapi(
                title=application.title,
                version=application.version,
                routes=application.routes,
                servers=application.servers,
                description=DESCRIPTION,
            )
            document["tags"] = [{"name": name, "description": text} for name, text in TAGS]
            document.setdefault("components", {}).setdefault("securitySchemes", {}).update(
                {
                    "ApiKey": {"type": "apiKey", "in": "header", "name": "X-Api-Key"},
                    "BrowserSession": {"type": "apiKey", "in": "cookie", "name": "editarr_session"},
                }
            )
            for path, operations in document["paths"].items():
                public = path in {
                    AUTH_JELLYFIN_LOGIN_PATH,
                    AUTH_SETUP_PATH,
                    AUTH_STATUS_PATH,
                    *AUTH_QUICK_CONNECT_PATHS,
                } or (path in SETUP_RESTORE_PATHS)
                for method, operation in operations.items():
                    if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                        operation["tags"] = [tag_for(path)]
                        _document_access(operation, public, method, path)
            application.openapi_schema = document
        return application.openapi_schema

    application.openapi = schema
