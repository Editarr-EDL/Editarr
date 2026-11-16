"""Document middleware authentication and preserve application-prefix servers."""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from scanner.security.auth_routes import (
    AUTH_JELLYFIN_LOGIN_PATH,
    AUTH_QUICK_CONNECT_PATHS,
    AUTH_SETUP_PATH,
    AUTH_STATUS_PATH,
)


def configure_api_documentation(application: FastAPI) -> None:
    """Expose X-Api-Key in Swagger without replacing the global access policy."""

    def schema():
        if application.openapi_schema is None:
            document = get_openapi(
                title=application.title,
                version=application.version,
                routes=application.routes,
                servers=application.servers,
                description="Local Editarr API. X-Api-Key grants administrator access. "
                "Browser sessions require same-origin CSRF protection for mutations.",
            )
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
                        operation["security"] = (
                            [] if public else [{"ApiKey": []}, {"BrowserSession": []}]
                        )
            application.openapi_schema = document
        return application.openapi_schema

    application.openapi = schema
