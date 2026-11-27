"""Document middleware authentication, curated tags, and application-prefix servers."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from scanner.security.access.route_match import route_interactive_only
from scanner.security.auth_routes import (
    AUTH_JELLYFIN_LOGIN_PATH,
    AUTH_QUICK_CONNECT_PATHS,
    AUTH_SETUP_PATH,
    AUTH_STATUS_PATH,
)

from .openapi_tags import TAGS, tag_for

DESCRIPTION = (
    "Local Editarr API. Send a personal key in the X-Api-Key header; a key carries the "
    "signed-in account's own current permissions, never more. Browser-session mutations "
    "must be same-origin and include the X-CSRF-Token header. Some account-security "
    "operations require an interactive browser session and return 403 "
    "interactive_session_required when called with an API key."
)
BROWSER_ONLY_NOTE = (
    "Requires an interactive browser session; API keys receive 403 interactive_session_required."
)


def _security(public: bool, browser_only: bool) -> list[dict[str, list[str]]]:
    if public:
        return []
    if browser_only:
        return [{"BrowserSession": []}]
    return [{"ApiKey": []}, {"BrowserSession": []}]


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
                } or (path.startswith("/api/setup-restore/"))
                for method, operation in operations.items():
                    if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                        operation["tags"] = [tag_for(path)]
                        browser_only = not public and route_interactive_only((method.upper(), path))
                        operation["security"] = _security(public, browser_only)
                        if browser_only:
                            description = operation.get("description")
                            operation["description"] = (
                                f"{description}\n\n{BROWSER_ONLY_NOTE}"
                                if description
                                else BROWSER_ONLY_NOTE
                            )
            application.openapi_schema = document
        return application.openapi_schema

    application.openapi = schema
