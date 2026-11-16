"""Development-only Vite/Uvicorn topology resolution and app factory."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from scanner.runtime.runtime_paths import bootstrap_entrypoint_runtime_paths

# Uvicorn imports this factory in a fresh reload process without importing the CLI.
bootstrap_entrypoint_runtime_paths()

from scanner.models import RestartCapability  # noqa: E402
from scanner.settings.config import Settings, settings  # noqa: E402
from scanner.settings.deployment_config import (  # noqa: E402
    DeploymentConfigurationError,
    DeploymentOverrides,
    DeploymentSnapshot,
    DeploymentStore,
    parse_base_url,
    resolve_deployment_snapshot,
    validate_port,
)


@dataclass(frozen=True, slots=True)
class DevelopmentTopology:
    """One non-persistent browser/API topology for Vite development."""

    snapshot: DeploymentSnapshot
    api_host: str
    web_host: str
    web_port: int

    @property
    def browser_origin(self) -> str:
        host = "localhost" if self.web_host == "0.0.0.0" else self.web_host
        return f"http://{host}:{self.web_port}"

    @property
    def browser_url(self) -> str:
        suffix = "/" if self.snapshot.prefix == "/" else f"{self.snapshot.prefix}/"
        return f"{self.browser_origin}{suffix}"

    @property
    def api_target(self) -> str:
        return f"http://{self.api_host}:{self.snapshot.listening_port}"

    @property
    def api_path(self) -> str:
        return "/api" if self.snapshot.prefix == "/" else f"{self.snapshot.prefix}/api"

    def child_environment(self, environment: Mapping[str, str]) -> dict[str, str]:
        child = dict(environment)
        child.setdefault("EDITARR_AUTH_COOKIE_SECURE", "false")
        child["EDITARR_DEVELOPMENT_RELOAD"] = "1"
        child["EDITARR_DEVELOPMENT_API_HOST"] = self.api_host
        child["EDITARR_DEVELOPMENT_API_PORT"] = str(self.snapshot.listening_port)
        child["EDITARR_DEVELOPMENT_API_PORT_SOURCE"] = self.snapshot.port_source
        child["EDITARR_DEVELOPMENT_WEB_HOST"] = self.web_host
        child["EDITARR_VITE_BASE_PATH"] = self.snapshot.prefix
        child["EDITARR_VITE_API_TARGET"] = self.api_target
        child["EDITARR_VITE_WEB_PORT"] = str(self.web_port)
        return child


def _environment_value(environment: Mapping[str, str], name: str) -> str | None:
    normalized = name.casefold()
    for key, value in environment.items():
        if key.casefold() == normalized:
            return value
    return None


def resolve_development_topology(
    *,
    configuration: Settings = settings,
    overrides: DeploymentOverrides | None = None,
    environment: Mapping[str, str] | None = None,
    api_host: str = "127.0.0.1",
    api_port: int | None = None,
    web_host: str = "localhost",
    web_port: int = 5173,
) -> DevelopmentTopology:
    """Resolve Vite and API paths without changing deployment.json."""

    environment = os.environ if environment is None else environment
    if api_host != "127.0.0.1":
        raise DeploymentConfigurationError(
            "Vite development requires the API to bind to 127.0.0.1; "
            "use './scripts/dev.sh api --api-host HOST' for API-only work"
        )
    if web_host not in {"localhost", "127.0.0.1", "0.0.0.0"}:
        raise DeploymentConfigurationError(
            "Use localhost, 127.0.0.1, or 0.0.0.0 for the development web host"
        )
    web_port = validate_port(web_port)
    secure_override = _environment_value(environment, "EDITARR_AUTH_COOKIE_SECURE")
    if secure_override is not None and secure_override.strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }:
        raise DeploymentConfigurationError(
            "Vite uses HTTP, but EDITARR_AUTH_COOKIE_SECURE requests secure cookies; "
            "unset it for development or use './scripts/dev.sh api'"
        )
    snapshot = resolve_deployment_snapshot(
        store=DeploymentStore(Path(configuration.data_dir)),
        overrides=overrides,
        cli_port_override=api_port,
        environment=environment,
        bind_host=api_host,
        # The Vite browser is an explicit loopback HTTP development context.
        auth_cookie_secure=False,
        auth_cookie_secure_overridden=True,
    )
    browser_host = "localhost" if web_host == "0.0.0.0" else web_host
    browser_origin = f"http://{browser_host}:{web_port}"
    if snapshot.listening_port == web_port:
        raise DeploymentConfigurationError(
            f"API port {snapshot.listening_port} conflicts with Vite port {web_port}; "
            "choose a different --api-port or --web-port"
        )
    if snapshot.base_url_source == "local_default":
        # Vite is the browser-facing local service. Keep this override entirely in
        # the immutable development snapshot; do not write it or mask a real seed.
        parsed = parse_base_url(browser_origin)
        snapshot = replace(
            snapshot,
            base_url=parsed.base_url,
            public_origin=parsed.public_origin,
            prefix=parsed.prefix,
        )
    else:
        expected = parse_base_url(f"{browser_origin}{snapshot.prefix}").base_url
        if snapshot.base_url != expected:
            raise DeploymentConfigurationError(
                f"The effective {snapshot.base_url_source} Base URL is {snapshot.base_url}, "
                f"but Vite would serve {expected}. Use './scripts/dev.sh api' to honor the "
                "configured URL, or use an isolated EDITARR_DATA_DIR/Base URL for Vite"
            )
    return DevelopmentTopology(
        snapshot=snapshot,
        api_host=api_host,
        web_host=web_host,
        web_port=web_port,
    )


def create_development_app():
    """Create the API-only ASGI app used by Uvicorn reload."""

    api_host = os.environ.get("EDITARR_DEVELOPMENT_API_HOST", "127.0.0.1")
    api_port_value = os.environ.get("EDITARR_DEVELOPMENT_API_PORT")
    topology = resolve_development_topology(
        api_host=api_host,
        api_port=int(api_port_value) if api_port_value else None,
        web_host=os.environ.get("EDITARR_DEVELOPMENT_WEB_HOST", "localhost"),
        web_port=int(os.environ.get("EDITARR_VITE_WEB_PORT", "5173")),
    )
    port_source = os.environ.get("EDITARR_DEVELOPMENT_API_PORT_SOURCE")
    if port_source is not None:
        if port_source not in {"cli", "saved", "default"}:
            raise DeploymentConfigurationError("Invalid development API port source")
        # Uvicorn owns the listener across reloads. Its fixed port is not itself
        # a user CLI override; retain the launcher's original saved/default policy.
        topology = replace(
            topology,
            snapshot=replace(
                topology.snapshot,
                port_source=port_source,
                cli_port_override=(
                    topology.snapshot.listening_port if port_source == "cli" else None
                ),
            ),
        )
    from scanner.cli import ServerLaunchContext
    from scanner.runtime import restart_service

    context = ServerLaunchContext(
        host=api_host,
        cli_port_override=topology.snapshot.cli_port_override,
        api_only=True,
        snapshot=topology.snapshot,
        original_argv=(),
    )
    restart_service.initial_snapshot = topology.snapshot
    from scanner.api import app, configure_http_service

    configure_http_service(app, context)
    app.state.development_topology = topology
    app.state.restart_capability = RestartCapability(
        supported=False,
        mode="unsupported",
        generation=0,
        in_progress=False,
        outcome="idle",
        reason="development_reload",
        detail="Use Uvicorn reload or restart the development command",
    )
    return app
