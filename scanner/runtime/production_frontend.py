"""Narrow production frontend serving for one Editarr HTTP generation.

The release task places the Vite output in ``scanner/frontend``.  This module
does not look in a checkout's ``web/dist`` directory: running a source checkout
without a packaged frontend is deliberately an API-only/development workflow.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import unquote_to_bytes

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.types import ASGIApp

from scanner.runtime.application_paths import application_relative_path
from scanner.settings.deployment_config import DeploymentSnapshot

_BOOTSTRAP_PATTERN: Final = re.compile(
    r"<script\b(?=[^>]*\bid\s*=\s*['\"]editarr-bootstrap['\"])(?=[^>]*\btype\s*=\s*['\"]application/json['\"])[^>]*>.*?</script>",
    re.IGNORECASE | re.DOTALL,
)
_HASHED_ASSET_PATTERN: Final = re.compile(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")
_ROOT_ASSETS: Final = frozenset(
    {
        "apple-touch-icon.png",
        "editarr-app-icon.png",
        "favicon-16.png",
        "favicon-32.png",
        "favicon.ico",
        "samwise-app-icon.png",
    }
)


class FrontendAssetsError(RuntimeError):
    """The installed production frontend is missing or incompatible."""


@dataclass(frozen=True, slots=True)
class FrontendAssets:
    """Validated packaged Vite output, resolved independently of the working directory."""

    root: Path
    index_template: str

    @classmethod
    def from_directory(cls, directory: Path) -> "FrontendAssets":
        """Validate built frontend assets and prepare the static serving configuration."""
        root = directory.resolve()
        index_path = root / "index.html"
        assets_path = root / "assets"
        if not root.is_dir():
            raise FrontendAssetsError(
                f"Production frontend assets are missing at {root}. "
                "Install a release containing bundled frontend assets, or use --api-only."
            )
        if not index_path.is_file() or index_path.is_symlink():
            raise FrontendAssetsError(
                f"Production frontend entry HTML is missing at {index_path}. "
                "Rebuild and install the release, or use --api-only."
            )
        if not assets_path.is_dir() or assets_path.is_symlink():
            raise FrontendAssetsError(
                f"Production frontend asset directory is missing at {assets_path}. "
                "Rebuild and install the release, or use --api-only."
            )
        try:
            index_template = index_path.read_text(encoding="utf-8")
        except OSError as error:
            raise FrontendAssetsError(f"Unable to read production entry HTML: {error}") from error
        if len(_BOOTSTRAP_PATTERN.findall(index_template)) != 1:
            raise FrontendAssetsError(
                "Production entry HTML has no compatible Editarr runtime bootstrap marker. "
                "Rebuild the frontend with the matching Editarr release."
            )
        if not any(
            path.is_file() and path.suffix in {".js", ".css"}
            for path in assets_path.iterdir()
        ):
            raise FrontendAssetsError(
                "Production frontend has no built JavaScript or CSS assets. "
                "Rebuild and install the release, or use --api-only."
            )
        return cls(root=root, index_template=index_template)

    def entry_html(self, snapshot: DeploymentSnapshot) -> bytes:
        """Render only the active generation's public, inert bootstrap data."""

        bootstrap = json.dumps(
            {
                "version": 1,
                "basePath": snapshot.prefix,
                "apiBasePath": "/api"
                if snapshot.prefix == "/"
                else f"{snapshot.prefix}/api",
            },
            separators=(",", ":"),
        )
        # Script-data JSON must not permit an HTML end tag, even though all values
        # currently come from validated deployment paths.
        bootstrap = (
            bootstrap.replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )
        rendered = _BOOTSTRAP_PATTERN.sub(
            lambda _match: (
                f'<script id="editarr-bootstrap" type="application/json">{bootstrap}</script>'
            ),
            self.index_template,
            count=1,
        )
        return rendered.encode("utf-8")


def packaged_frontend_directory() -> Path:
    """Return the frozen installed-package asset location."""

    return Path(__file__).resolve().parents[1] / "frontend"


def load_packaged_frontend() -> FrontendAssets:
    """Load the release artifact's frontend, never a cwd-relative development build."""

    return FrontendAssets.from_directory(packaged_frontend_directory())


def configure_frontend(application: object, *, api_only: bool) -> None:
    """Install the launch mode's static asset state before the first request."""

    setattr(application.state, "frontend_assets", None if api_only else load_packaged_frontend())


def served_roots(application: object) -> tuple[Path, ...]:
    """Directories whose files static HTTP serves now or would serve in another launch mode."""

    frontend = getattr(application.state, "frontend_assets", None)
    active = (frontend.root,) if isinstance(frontend, FrontendAssets) else ()
    return (packaged_frontend_directory(), *active)


class ProductionFrontendMiddleware(BaseHTTPMiddleware):
    """Expose only entry HTML and a fixed packaged asset surface before auth."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        """Serve frontend shell and assets while passing API requests to the backend."""
        frontend = getattr(request.app.state, "frontend_assets", None)
        if not isinstance(frontend, FrontendAssets):
            return await call_next(request)
        snapshot = getattr(request.app.state, "deployment_snapshot", None)
        if not isinstance(snapshot, DeploymentSnapshot):
            return await call_next(request)
        path = application_relative_path(request, snapshot)
        if path is None:
            return await call_next(request)

        if path == "/":
            return self._entry_response(request, frontend, snapshot)
        relative = self._asset_relative_path(path)
        if relative is None:
            return await call_next(request)
        if request.method not in {"GET", "HEAD"}:
            return Response(status_code=405, headers={"Allow": "GET, HEAD"})
        asset_path = self._safe_asset_path(frontend.root, relative, request)
        if asset_path is None:
            return Response(status_code=404)
        cache_control = (
            "public, max-age=31536000, immutable"
            if _HASHED_ASSET_PATTERN.search(relative.name)
            else "no-cache"
        )
        return FileResponse(asset_path, headers={"Cache-Control": cache_control})

    @staticmethod
    def _entry_response(
        request: Request, frontend: FrontendAssets, snapshot: DeploymentSnapshot
    ) -> Response:
        if request.method not in {"GET", "HEAD"}:
            return Response(status_code=405, headers={"Allow": "GET, HEAD"})
        body = frontend.entry_html(snapshot)
        return Response(
            content=b"" if request.method == "HEAD" else body,
            media_type="text/html",
            headers={"Cache-Control": "no-store", "Content-Length": str(len(body))},
        )

    @staticmethod
    def _asset_relative_path(path: str) -> PurePosixPath | None:
        candidate = path.removeprefix("/")
        if candidate in _ROOT_ASSETS:
            return PurePosixPath(candidate)
        if candidate.startswith("assets/"):
            return PurePosixPath(candidate)
        return None

    @staticmethod
    def _safe_asset_path(root: Path, relative: PurePosixPath, request: Request) -> Path | None:
        """Resolve an asset name beneath the build root without path traversal."""
        raw_path = request.scope.get("raw_path", b"")
        try:
            raw_decoded = unquote_to_bytes(raw_path).decode("utf-8", "strict")
        except UnicodeDecodeError:
            return None
        if "%2f" in raw_path.decode("latin-1").casefold() or "%5c" in raw_path.decode(
            "latin-1"
        ).casefold():
            return None
        parts = relative.parts
        if (
            not parts
            or any(
                part in {"", ".", ".."} or "\\" in part or any(ord(c) < 32 for c in part)
                for part in parts
            )
            or "\\" in raw_decoded
        ):
            return None
        candidate = root
        for part in parts:
            candidate = candidate / part
            if candidate.is_symlink():
                return None
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        return resolved if resolved.is_file() else None
