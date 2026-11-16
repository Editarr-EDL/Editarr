"""Canonical paths for one immutable Editarr HTTP-service generation."""

from __future__ import annotations

from fastapi import Request

from scanner.settings.deployment_config import DeploymentSnapshot


def application_url(snapshot: DeploymentSnapshot, path: str) -> str:
    """Return one origin-relative application URL beneath the active prefix.

    Callers supply an application-relative route (``/api/...``), never a public
    origin.  Keeping that boundary small avoids accidentally turning a Host or a
    forwarded header into an application URL.
    """

    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("application path must be a single-slash absolute path")
    if snapshot.prefix == "/":
        return path
    return f"{snapshot.prefix}{path}"


def application_relative_path(
    request: Request, snapshot: DeploymentSnapshot
) -> str | None:
    """Return the route path beneath the configured mount, or ``None`` outside it."""

    path = request.scope["path"]
    if snapshot.prefix == "/":
        return path
    if path == snapshot.prefix:
        return "/"
    if not path.startswith(f"{snapshot.prefix}/"):
        return None
    return path[len(snapshot.prefix) :]


def unique_cookie_value(request: Request, name: str) -> str | None:
    """Return one named cookie value, rejecting ambiguous same-name cookies.

    Cookie paths are not included in the Cookie request header.  During a prefix
    transition a browser can otherwise send both the old root cookie and the new
    prefix cookie, and different parsers disagree about which one wins.
    """

    values: list[str] = []
    for header_name, header_value in request.scope.get("headers", ()):
        if header_name.lower() != b"cookie":
            continue
        for item in header_value.decode("latin-1").split(";"):
            candidate_name, separator, candidate_value = item.strip().partition("=")
            if separator and candidate_name == name:
                values.append(candidate_value)
    return values[0] if len(values) == 1 else None
