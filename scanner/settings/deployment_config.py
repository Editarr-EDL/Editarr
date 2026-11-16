"""Persist and resolve the deployment address independently of authentication settings.

The deployment file deliberately contains only explicit operator overrides.  Its
derived effective URL is never written back, so a port-only save cannot accidentally
turn an environment seed (or the localhost default) into a permanent override.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Literal, Mapping, cast
from urllib.parse import urlsplit

from scanner.runtime.maintenance import DatabaseMaintenance
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.security.auth_origin import OriginParseError, PublicOrigin, parse_public_origin

try:  # POSIX is a supported deployment target; keep imports usable on Windows tests.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX platforms
    fcntl = None  # type: ignore[assignment]


_VERSION = 1
_FILENAME = "deployment.json"
_LOCK_FILENAME = "deployment.json.lock"
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+\Z")
_UNSET = object()
DEFAULT_LISTENING_PORT = 3348


class DeploymentConfigurationError(ValueError):
    """A deployment value is invalid or the persisted configuration is corrupt."""


class DeploymentConfigurationUnavailable(RuntimeError):
    """The deployment configuration cannot safely be read or atomically written."""


@dataclass(frozen=True, slots=True)
class ParsedBaseURL:
    """One canonical public URL, its origin tuple, and its mounted path prefix."""

    base_url: str
    public_origin: PublicOrigin
    prefix: str


@dataclass(frozen=True, slots=True)
class CookieScope:
    """Non-secret session-transition metadata, activated by the restart controller."""

    not_before: datetime | None = None
    legacy_paths: tuple[str, ...] = ()
    pending_previous_path: str | None = None
    activated_base_url: str | None = None
    activated_local_default: bool = False


@dataclass(frozen=True, slots=True)
class DeploymentOverrides:
    """Only fields explicitly saved by the operator, never effective fallbacks."""

    base_url: str | None = None
    port: int | None = None
    cookie_scope: CookieScope = CookieScope()


@dataclass(frozen=True, slots=True)
class DeploymentSnapshot:
    """Immutable configuration for a single HTTP-service generation."""

    base_url: str
    public_origin: PublicOrigin
    prefix: str
    listening_port: int
    base_url_source: Literal["saved", "environment", "local_default"]
    port_source: Literal["cli", "saved", "default"]
    cli_port_override: int | None
    cookie_secure: bool
    session_not_before: datetime | None
    legacy_cookie_paths: tuple[str, ...]


def parse_base_url(value: str) -> ParsedBaseURL:
    """Strictly parse an absolute HTTP(S) Base URL without changing Origin grammar."""

    if not isinstance(value, str) or not value:
        raise DeploymentConfigurationError("Base URL is required")
    if any(
        ord(character) <= 32 or ord(character) == 127 or character == "\\" for character in value
    ):
        raise DeploymentConfigurationError("Base URL contains whitespace, controls, or a backslash")
    try:
        parsed = urlsplit(value)
        # Accessing port makes urlsplit reject malformed and out-of-range ports.
        _ = parsed.port
    except ValueError as error:
        raise DeploymentConfigurationError("Base URL has an invalid host or port") from error
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DeploymentConfigurationError("Base URL must use HTTP(S) and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise DeploymentConfigurationError("Base URL must not contain userinfo")
    if "?" in value or "#" in value:
        raise DeploymentConfigurationError("Base URL must not contain a query or fragment")
    try:
        origin = parse_public_origin(f"{parsed.scheme}://{parsed.netloc}")
    except OriginParseError as error:
        raise DeploymentConfigurationError(f"Base URL origin is invalid: {error}") from error
    prefix = _canonical_prefix(parsed.path)
    return ParsedBaseURL(
        base_url=origin.serialized if prefix == "/" else f"{origin.serialized}{prefix}",
        public_origin=origin,
        prefix=prefix,
    )


def validate_port(value: int) -> int:
    """Validate a listening-port override without accepting bool as an integer."""

    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise DeploymentConfigurationError("port must be an integer in 1..65535")
    return value


def _canonical_prefix(path: str) -> str:
    """Normalize a URL path prefix and reject malformed or unsafe segments."""
    if not path or path == "/":
        return "/"
    segments = path.split("/")
    if not path.startswith("/") or any(segment == "" for segment in segments[1:-1]):
        raise DeploymentConfigurationError("Base URL path must not contain empty path segments")
    canonical_segments = segments[1:-1] if segments[-1] == "" else segments[1:]
    if any(segment in {".", ".."} for segment in canonical_segments):
        raise DeploymentConfigurationError("Base URL path must not contain traversal segments")
    if any(_PATH_SEGMENT.fullmatch(segment) is None for segment in canonical_segments):
        raise DeploymentConfigurationError(
            "Base URL path segments may contain only ASCII letters, digits, -, ., _, or ~; "
            "percent escapes and other characters are unsupported"
        )
    return path.rstrip("/") or "/"


class DeploymentStore:
    """Read and atomically update `${data_dir}/deployment.json` under a file lock."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / _FILENAME
        self._lock_path = self._data_dir / _LOCK_FILENAME
        self._thread_lock = RLock()

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> DeploymentOverrides:
        if not self._data_dir.exists():
            return DeploymentOverrides()
        if not self._data_dir.is_dir():
            raise DeploymentConfigurationUnavailable(
                "Deployment configuration directory is not a directory"
            )
        with self._locked(create_directory=False):
            return self._read_unlocked()

    def update(
        self,
        *,
        base_url: str | None | object = _UNSET,
        port: int | None | object = _UNSET,
        cookie_scope: CookieScope | object = _UNSET,
    ) -> DeploymentOverrides:
        """Validate a merged update then replace the file atomically.

        Omitted fields are retained.  ``None`` clears a saved base URL or port;
        callers cannot select a persistence location.
        """

        with self._locked(create_directory=True):
            current = self._read_unlocked()
            proposed = self._proposed_overrides(
                current,
                base_url=base_url,
                port=port,
                cookie_scope=cookie_scope,
            )
            self._write_unlocked(proposed)
            return proposed

    def activate(self, snapshot: DeploymentSnapshot) -> DeploymentSnapshot:
        """Commit scope after startup/bind succeeds, before serving requests.

        Remember the last activated scope for offline edits and environment changes.
        Port-only changes between implicit local scopes retain sessions.
        """
        with self._locked(create_directory=True):
            current = self._read_unlocked()
            scope = current.cookie_scope
            previous = (
                parse_base_url(scope.activated_base_url)
                if scope.activated_base_url is not None else None
            )
            changed = (
                previous is not None
                and (previous.public_origin != snapshot.public_origin
                     or previous.prefix != snapshot.prefix)
                and not (scope.activated_local_default
                         and snapshot.base_url_source == "local_default")
            )
            changed = changed or (previous is None and scope.pending_previous_path is not None)
            cutoff = scope.not_before
            paths = list(scope.legacy_paths)
            if changed:
                cutoff = (
                    max(datetime.now(UTC), cutoff + timedelta(microseconds=1))
                    if cutoff else datetime.now(UTC)
                )
                old_path = previous.prefix if previous else scope.pending_previous_path
                if old_path is not None and old_path not in paths:
                    paths.append(old_path)
            activated = replace(
                scope, not_before=cutoff, legacy_paths=tuple(paths),
                pending_previous_path=None, activated_base_url=snapshot.base_url,
                activated_local_default=snapshot.base_url_source == "local_default",
            )
            self._write_unlocked(replace(current, cookie_scope=activated))
            return replace(snapshot, session_not_before=cutoff, legacy_cookie_paths=tuple(paths))

    def preview_update(
        self,
        current: DeploymentOverrides,
        *,
        base_url: str | None | object = _UNSET,
        port: int | None | object = _UNSET,
        cookie_scope: CookieScope | object = _UNSET,
    ) -> DeploymentOverrides:
        """Validate and merge an update without touching the deployment file.

        Settings uses this to resolve the complete next-start configuration before
        persisting anything.  Keeping it beside ``update`` means callers do not
        recreate field semantics or URL parsing just to reject an invalid combined
        change atomically.
        """

        if not isinstance(current, DeploymentOverrides):
            raise TypeError("current must be DeploymentOverrides")
        return self._proposed_overrides(
            current,
            base_url=base_url,
            port=port,
            cookie_scope=cookie_scope,
        )

    @staticmethod
    def _proposed_overrides(
        current: DeploymentOverrides,
        *,
        base_url: str | None | object,
        port: int | None | object,
        cookie_scope: CookieScope | object,
    ) -> DeploymentOverrides:
        """Return one validated merged override object for preview or persistence."""

        if base_url is not _UNSET and base_url is not None:
            if not isinstance(base_url, str) or not base_url:
                raise DeploymentConfigurationError("Base URL must be a non-empty string or null")
            base_url = parse_base_url(base_url).base_url
        if port is not _UNSET and port is not None:
            port = validate_port(cast(int, port))
        if cookie_scope is not _UNSET and not isinstance(cookie_scope, CookieScope):
            raise DeploymentConfigurationError("cookie scope is invalid")
        return DeploymentOverrides(
            base_url=current.base_url if base_url is _UNSET else cast(str | None, base_url),
            port=current.port if port is _UNSET else cast(int | None, port),
            cookie_scope=(
                current.cookie_scope
                if cookie_scope is _UNSET
                else _merge_cookie_scope(current.cookie_scope, cast(CookieScope, cookie_scope))
            ),
        )

    def _read_unlocked(self) -> DeploymentOverrides:
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return DeploymentOverrides()
        except (OSError, json.JSONDecodeError) as error:
            raise DeploymentConfigurationUnavailable(
                f"Unable to read deployment configuration: {error}"
            ) from error
        return _decode_overrides(raw)

    def _write_unlocked(self, overrides: DeploymentOverrides) -> None:
        """Atomically replace the deployment configuration while the lock is held."""
        payload = _encode_overrides(overrides)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".deployment-", suffix=".tmp", dir=self._data_dir
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
            try:
                self._path.chmod(0o600)
            except OSError:
                pass
            temporary_path = None
        except OSError as error:
            raise DeploymentConfigurationUnavailable(
                f"Unable to write deployment configuration: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    class _Lock:
        def __init__(self, store: "DeploymentStore", *, create_directory: bool) -> None:
            self._store = store
            self._create_directory = create_directory
            self._handle = None
            self._lease = ExitStack()

        def __enter__(self) -> None:
            """Acquire the deployment configuration lock for a read or update transaction."""
            self._lease.enter_context(DatabaseMaintenance(self._store._data_dir).worker_lease())
            acquired = False
            try:
                self._store._thread_lock.acquire()
                acquired = True
                if self._create_directory:
                    ensure_user_private_directory(self._store._data_dir)
                self._handle = self._store._lock_path.open("a+", encoding="utf-8")
                try:
                    self._store._lock_path.chmod(0o600)
                except OSError:
                    pass
                if fcntl is not None:
                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
            except BaseException as error:
                if acquired:
                    self._store._thread_lock.release()
                self._lease.close()
                if isinstance(error, OSError):
                    raise DeploymentConfigurationUnavailable(
                        f"Unable to lock deployment configuration: {error}"
                    ) from error
                raise

        def __exit__(self, *_: object) -> None:
            try:
                if self._handle is not None:
                    if fcntl is not None:
                        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                    self._handle.close()
            finally:
                try:
                    self._store._thread_lock.release()
                finally:
                    self._lease.close()

    def _locked(self, *, create_directory: bool) -> "DeploymentStore._Lock":
        return self._Lock(self, create_directory=create_directory)


def _decode_overrides(raw: object) -> DeploymentOverrides:
    """Validate persisted deployment overrides before applying them."""
    if not isinstance(raw, dict) or set(raw) - {"version", "base_url", "port", "cookie_scope"}:
        raise DeploymentConfigurationError("deployment configuration has an invalid schema")
    if raw.get("version") != _VERSION:
        raise DeploymentConfigurationError("deployment configuration has an unsupported version")
    base_url = raw.get("base_url")
    if "base_url" in raw and not isinstance(base_url, str):
        raise DeploymentConfigurationError("deployment configuration base_url must be a string")
    if base_url is not None:
        base_url = parse_base_url(base_url).base_url
    port = raw.get("port")
    if "port" in raw and port is None:
        raise DeploymentConfigurationError("deployment configuration port must be an integer")
    if port is not None:
        port = validate_port(port)
    cookie_scope_raw = raw.get("cookie_scope")
    cookie_scope = _decode_cookie_scope(cookie_scope_raw)
    return DeploymentOverrides(base_url=base_url, port=port, cookie_scope=cookie_scope)


def _decode_cookie_scope(raw: object) -> CookieScope:
    """Decode and validate the persisted cookie path scope."""
    if raw is None:
        return CookieScope()
    if not isinstance(raw, dict) or set(raw) - {
        "not_before",
        "legacy_paths",
        "pending_previous_path",
        "activated_base_url",
        "activated_local_default",
    }:
        raise DeploymentConfigurationError("deployment configuration cookie_scope is invalid")
    legacy_paths_raw = raw.get("legacy_paths")
    if not isinstance(legacy_paths_raw, list) or not all(
        isinstance(path, str) for path in legacy_paths_raw
    ):
        raise DeploymentConfigurationError("deployment configuration legacy_paths is invalid")
    legacy_paths = tuple(_canonical_prefix(path) for path in legacy_paths_raw)
    if len(set(legacy_paths)) != len(legacy_paths):
        raise DeploymentConfigurationError(
            "deployment configuration legacy_paths contains duplicates"
        )
    pending = raw.get("pending_previous_path")
    if pending is not None and not isinstance(pending, str):
        raise DeploymentConfigurationError(
            "deployment configuration pending_previous_path is invalid"
        )
    not_before_raw = raw.get("not_before")
    if not_before_raw is not None and not isinstance(not_before_raw, str):
        raise DeploymentConfigurationError("deployment configuration not_before is invalid")
    not_before = _parse_timestamp(not_before_raw) if not_before_raw is not None else None
    activated = raw.get("activated_base_url")
    if activated is not None:
        activated = parse_base_url(activated).base_url
    local_default = raw.get("activated_local_default", False)
    if not isinstance(local_default, bool):
        raise DeploymentConfigurationError("activated_local_default must be boolean")
    return CookieScope(
        activated_base_url=activated,
        activated_local_default=local_default,
        not_before=not_before,
        legacy_paths=legacy_paths,
        pending_previous_path=_canonical_prefix(pending) if pending is not None else None,
    )


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DeploymentConfigurationError(
            "deployment configuration not_before is not RFC3339"
        ) from error
    if parsed.tzinfo is None:
        raise DeploymentConfigurationError(
            "deployment configuration not_before must include a timezone"
        )
    return parsed.astimezone(UTC)


def _merge_cookie_scope(current: CookieScope, proposed: CookieScope) -> CookieScope:
    """Normalize transition metadata and prevent a cutoff from moving backward."""

    not_before = proposed.not_before
    if not_before is not None:
        if not isinstance(not_before, datetime) or not_before.tzinfo is None:
            raise DeploymentConfigurationError(
                "cookie scope not_before must be a timezone-aware datetime"
            )
        not_before = not_before.astimezone(UTC)
    if current.not_before is not None:
        if not_before is None:
            not_before = current.not_before
        elif not_before < current.not_before:
            raise DeploymentConfigurationError("cookie scope not_before must not move backward")
    legacy_paths: list[str] = []
    for path in proposed.legacy_paths:
        if not isinstance(path, str):
            raise DeploymentConfigurationError("cookie scope legacy paths must be strings")
        canonical_path = _canonical_prefix(path)
        if canonical_path not in legacy_paths:
            legacy_paths.append(canonical_path)
    pending = proposed.pending_previous_path
    if pending is not None:
        if not isinstance(pending, str):
            raise DeploymentConfigurationError("cookie scope pending previous path is invalid")
        pending = _canonical_prefix(pending)
    return replace(
        proposed,
        not_before=not_before,
        legacy_paths=tuple(legacy_paths),
        pending_previous_path=pending,
    )


def _encode_overrides(overrides: DeploymentOverrides) -> dict[str, object]:
    """Serialize deployment overrides into the persisted configuration shape."""
    payload: dict[str, object] = {"version": _VERSION}
    if overrides.base_url is not None:
        payload["base_url"] = overrides.base_url
    if overrides.port is not None:
        payload["port"] = overrides.port
    scope = overrides.cookie_scope
    if scope != CookieScope():
        cookie_scope: dict[str, object] = {"legacy_paths": list(scope.legacy_paths)}
        if scope.not_before is not None:
            cookie_scope["not_before"] = (
                scope.not_before.astimezone(UTC).isoformat().replace("+00:00", "Z")
            )
        if scope.pending_previous_path is not None:
            cookie_scope["pending_previous_path"] = scope.pending_previous_path
        if scope.activated_base_url is not None:
            cookie_scope["activated_base_url"] = scope.activated_base_url
            cookie_scope["activated_local_default"] = scope.activated_local_default
        payload["cookie_scope"] = cookie_scope
    return payload


def resolve_deployment_snapshot(
    *,
    store: DeploymentStore,
    overrides: DeploymentOverrides | None = None,
    cli_port_override: int | None = None,
    environment: Mapping[str, str] | None = None,
    bind_host: str = "127.0.0.1",
    auth_cookie_secure: bool = True,
    auth_cookie_secure_overridden: bool = False,
) -> DeploymentSnapshot:
    """Resolve one next-start snapshot without mutating Settings or the saved file."""

    overrides = store.read() if overrides is None else overrides
    if cli_port_override is not None:
        port = validate_port(cli_port_override)
        port_source: Literal["cli", "saved", "default"] = "cli"
    elif overrides.port is not None:
        port = overrides.port
        port_source = "saved"
    else:
        port = DEFAULT_LISTENING_PORT
        port_source = "default"

    if overrides.base_url is not None:
        parsed = parse_base_url(overrides.base_url)
        base_url_source: Literal["saved", "environment", "local_default"] = "saved"
    else:
        environment_seed = _environment_value(
            environment if environment is not None else os.environ, "EDITARR_BASE_URL"
        )
        if environment_seed is not None:
            parsed = parse_base_url(environment_seed)
            base_url_source = "environment"
        else:
            if bind_host not in {"127.0.0.1", "0.0.0.0", "::"}:
                raise DeploymentConfigurationError(
                    "A Base URL is required unless binding to loopback or all interfaces"
                )
            parsed = parse_base_url(f"http://localhost:{port}/")
            base_url_source = "local_default"

    if base_url_source == "local_default":
        if auth_cookie_secure_overridden and auth_cookie_secure:
            raise DeploymentConfigurationError(
                "EDITARR_AUTH_COOKIE_SECURE=true conflicts with local HTTP deployment"
            )
        cookie_secure = False
    elif parsed.public_origin.scheme == "http":
        if auth_cookie_secure or not auth_cookie_secure_overridden:
            raise DeploymentConfigurationError(
                "An HTTP Base URL requires EDITARR_AUTH_COOKIE_SECURE=false"
            )
        cookie_secure = False
    else:
        cookie_secure = True

    return DeploymentSnapshot(
        base_url=parsed.base_url,
        public_origin=parsed.public_origin,
        prefix=parsed.prefix,
        listening_port=port,
        base_url_source=base_url_source,
        port_source=port_source,
        cli_port_override=cli_port_override,
        cookie_secure=cookie_secure,
        session_not_before=overrides.cookie_scope.not_before,
        legacy_cookie_paths=overrides.cookie_scope.legacy_paths,
    )


def _environment_value(environment: Mapping[str, str], name: str) -> str | None:
    normalized = name.casefold()
    for key, value in environment.items():
        if key.casefold() == normalized:
            return value
    return None
