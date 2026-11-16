"""Private per-user launcher coordination for packaged Editarr applications.

The launcher is intentionally separate from the HTTP API.  Its only local IPC
commands are lifecycle actions needed by a package shell; it never accepts TCP
connections and its random capability is stored in an owner-private state file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import webbrowser
from dataclasses import dataclass
from multiprocessing.context import AuthenticationError
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Callable

try:  # POSIX launcher lock; Windows uses msvcrt below.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows
    fcntl = None  # type: ignore[assignment]

from scanner.runtime.process_control import connect_private_endpoint, create_private_listener
from scanner.runtime.runtime_paths import RuntimePaths, ensure_user_private_directory

logger = logging.getLogger(__name__)
INSTANCE_START_TIMEOUT = 5.0
CONTROL_TIMEOUT = 1.0


class NativeLifecycleError(RuntimeError):
    """The native launcher cannot safely coordinate a local instance."""


@dataclass(frozen=True, slots=True)
class InstanceMetadata:
    """The minimum private state needed to prove and control one launcher."""

    pid: int
    endpoint: str
    token: str
    url: str
    ready: bool

    def encode(self) -> bytes:
        """Encode a controller message with its version and authentication fields."""
        return json.dumps(
            {
                "version": 1,
                "pid": self.pid,
                "endpoint": self.endpoint,
                "token": self.token,
                "url": self.url,
                "ready": self.ready,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def decode(cls, value: bytes) -> "InstanceMetadata":
        """Decode and validate a controller message from private IPC."""
        try:
            parsed = json.loads(value.decode("utf-8"))
            if (
                parsed.get("version") != 1
                or isinstance(parsed["pid"], bool)
                or not isinstance(parsed["pid"], int)
                or parsed["pid"] <= 0
                or not all(
                    isinstance(parsed[key], str) and parsed[key]
                    for key in ("endpoint", "token", "url")
                )
                or not isinstance(parsed["ready"], bool)
            ):
                raise ValueError
            return cls(
                pid=parsed["pid"], endpoint=parsed["endpoint"], token=parsed["token"],
                url=parsed["url"], ready=parsed["ready"],
            )
        except (KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            raise NativeLifecycleError("native instance state is invalid") from None


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()[:32]


def _endpoint(paths: RuntimePaths, token: str, *, platform_name: str | None = None) -> Path | str:
    """Return a random, token-derived same-user local endpoint name."""

    name = os.name if platform_name is None else platform_name
    digest = _token_digest(token)
    if name == "nt":
        return rf"\\.\pipe\EditarrLauncher-{digest}"
    root = Path(paths.temp_dir) / "editarr-launcher-control"
    # Match the short AF_UNIX fallback used by worker/controller control sockets.
    if len(os.fsencode(str(root))) > 55:
        root = Path("/tmp") / f"editarr-ipc-{os.getuid()}" / "editarr-launcher-control"
    return ensure_user_private_directory(root) / f"instance-{digest}.sock"


def _safe_unlink(path: Path) -> None:
    """Remove only launcher-owned regular files or sockets, never a surprise path."""

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISREG(mode) or stat.S_ISSOCK(mode):
        path.unlink()


class NativeLifecycleAdapter:
    """The small package-shell action seam; no web API or machine service is used."""

    def __init__(
        self,
        *,
        browser_opener: Callable[[str], bool] | None = None,
        log_opener: Callable[[Path], object] | None = None,
    ) -> None:
        """Initialize the native lifecycle controller and its private IPC endpoint."""
        self._browser_opener = browser_opener or webbrowser.open
        self._log_opener = log_opener or _open_logs_directory

    def open_ui(self, url: str) -> bool:
        try:
            return bool(self._browser_opener(url))
        except Exception:
            logger.warning("Unable to open Editarr UI in the default browser", exc_info=True)
            return False

    def open_logs(self, log_dir: Path) -> bool:
        try:
            self._log_opener(log_dir)
            return True
        except Exception:
            logger.warning("Unable to open the Editarr log folder", exc_info=True)
            return False

    def quit(self, stop_event: Event) -> None:
        stop_event.set()


def _open_logs_directory(path: Path) -> object:
    if sys.platform == "darwin":
        return subprocess.Popen(["open", str(path)])
    if os.name == "nt":
        return os.startfile(str(path))  # type: ignore[attr-defined]
    return subprocess.Popen(["xdg-open", str(path)])


class NativeInstance:
    """Owner or client-side view of the current per-user native instance."""

    def __init__(self, paths: RuntimePaths, adapter: NativeLifecycleAdapter | None = None) -> None:
        """Initialize the native lifecycle controller and its private IPC endpoint."""
        self.paths = paths
        self.adapter = adapter or NativeLifecycleAdapter()
        self.directory = ensure_user_private_directory(Path(paths.data_dir) / "native-instance")
        self.lock_path = self.directory / "instance.lock"
        self.state_path = self.directory / "instance.json"
        self._lock_fd: int | None = None
        self._metadata: InstanceMetadata | None = None
        self._listener = None
        self._closed = Event()
        self._quit = Event()
        self._open_requested = Event()
        self._listener_thread: Thread | None = None
        self._guard = Lock()

    @property
    def quit_event(self) -> Event:
        return self._quit

    @property
    def owns_instance(self) -> bool:
        return self._lock_fd is not None

    def claim_or_open_existing(self, url: str, *, open_ui: bool) -> bool:
        """Return true only for the one process that now owns the instance.

        A second launch must authenticate the running endpoint before it asks for a
        browser action. Corrupt state is ignored without ever signaling the recorded
        PID, while the kernel lock decides whether stale ownership can be reclaimed.
        """

        deadline = time.monotonic() + INSTANCE_START_TIMEOUT
        while True:
            if self._try_acquire_lock():
                try:
                    self._become_owner(url)
                except BaseException:
                    self.close()
                    raise
                return True
            response = self._request_existing("open-ui" if open_ui else "status")
            if response is not None:
                return False
            if time.monotonic() >= deadline:
                # A held kernel lock is stronger evidence than a PID or state
                # file. Never unlink it underneath a live-but-slow owner: doing
                # so would let two concurrent launches both become controller.
                raise NativeLifecycleError(
                    "another Editarr launcher owns the instance but its control endpoint "
                    "did not become ready"
                )
            time.sleep(0.05)

    def _try_acquire_lock(self) -> bool:
        """Hold a kernel lock for the complete owner lifetime.

        The lock file is deliberately persistent. A crashed process releases the
        kernel lock automatically, so stale recovery never needs to unlink a path
        that another process may still have locked.
        """

        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(self.lock_path, 0o600)
        except OSError:
            pass
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                    os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                assert fcntl is not None
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _become_owner(self, url: str) -> None:
        """Claim ownership of the current native application instance."""
        token = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
        endpoint = _endpoint(self.paths, token)
        listener = create_private_listener(endpoint, token.encode("ascii"))
        if isinstance(endpoint, Path):
            os.chmod(endpoint, 0o600)
        if hasattr(listener, "prepare"):
            listener.prepare()
        self._metadata = InstanceMetadata(os.getpid(), str(endpoint), token, url, False)
        self._listener = listener
        self._write_metadata()
        self._listener_thread = Thread(
            target=self._serve, name="editarr-native-launcher", daemon=True
        )
        self._listener_thread.start()

    def ready(self, url: str, *, open_ui: bool) -> None:
        """Publish the ready URL and open it only after controller readiness."""

        with self._guard:
            if self._metadata is None:
                return
            first_readiness = not self._metadata.ready
            self._metadata = InstanceMetadata(
                self._metadata.pid, self._metadata.endpoint, self._metadata.token, url, True
            )
            self._write_metadata()
        if first_readiness and (open_ui or self._open_requested.is_set()):
            self._open_requested.clear()
            self.adapter.open_ui(url)

    def status(self) -> dict[str, object] | None:
        """Return authenticated status for the running per-user instance."""

        return self._request_existing("status")

    def activate_existing(self, *, open_ui: bool) -> bool:
        """Activate an existing instance without resolving new server settings.

        This lets a second application launch use the owner's already-activated URL
        even if pending deployment configuration has since become invalid.
        """

        action = "open-ui" if open_ui else "status"
        return self._request_existing(action) is not None

    def open_ui(self) -> bool:
        """Ask the authenticated running instance to open its active public URL."""

        return self._request_existing("open-ui") is not None

    def open_logs(self) -> bool:
        """Ask the authenticated running instance to reveal its native log folder."""

        return self._request_existing("open-logs") is not None

    def quit(self) -> bool:
        """Ask the authenticated owner to perform full application Quit."""

        return self._request_existing("quit") is not None

    def _serve(self) -> None:
        """Serve authenticated requests from the private lifecycle endpoint."""
        assert self._listener is not None
        while not self._closed.is_set():
            try:
                connection = self._listener.accept()
            except (AuthenticationError, EOFError, OSError):
                if self._closed.is_set():
                    return
                continue
            try:
                request = connection.recv()
                if not isinstance(request, str) or request not in {
                    "status",
                    "open-ui",
                    "open-logs",
                    "quit",
                }:
                    connection.send({"ok": False})
                    continue
                with self._guard:
                    metadata = self._metadata
                if metadata is None:
                    connection.send({"ok": False})
                    continue
                if request == "open-ui":
                    if metadata.ready:
                        self.adapter.open_ui(metadata.url)
                    else:
                        # A concurrent second launch requests a browser action
                        # after readiness, never while the listener is binding.
                        self._open_requested.set()
                    connection.send(
                        {
                            "ok": True,
                            "pid": metadata.pid,
                            "url": metadata.url,
                            "ready": metadata.ready,
                        }
                    )
                elif request == "open-logs":
                    connection.send(
                        {
                            "ok": self.adapter.open_logs(Path(self.paths.log_dir)),
                            "pid": metadata.pid,
                        }
                    )
                elif request == "quit":
                    self.adapter.quit(self._quit)
                    connection.send({"ok": True, "pid": metadata.pid})
                else:
                    connection.send(
                        {
                            "ok": True,
                            "pid": metadata.pid,
                            "url": metadata.url,
                            "ready": metadata.ready,
                        }
                    )
            except (EOFError, OSError, ValueError, TypeError):
                continue
            finally:
                if hasattr(connection, "close_server"):
                    connection.close_server()
                else:
                    connection.close()

    def _request_existing(self, action: str) -> dict[str, object] | None:
        """Send a command to the running native controller and read its response."""
        try:
            metadata = InstanceMetadata.decode(self.state_path.read_bytes())
            # The endpoint is derived from the opaque capability, not independently
            # chosen state.  Refuse a corrupt/tampered record before it can redirect
            # this launcher to another same-user IPC endpoint.
            if metadata.endpoint != str(_endpoint(self.paths, metadata.token)):
                return None
            connection = connect_private_endpoint(metadata.endpoint, metadata.token.encode("ascii"))
            try:
                connection.send(action)
                if not connection.poll(CONTROL_TIMEOUT):
                    return None
                response = connection.recv()
            finally:
                connection.close()
            if not isinstance(response, dict) or response.get("ok") is not True:
                return None
            # Authenticated control and exact current PID prevent a stale state
            # record from being confused with an unrelated recycled PID.
            if response.get("pid") != metadata.pid:
                return None
            return response
        except (
            AuthenticationError,
            OSError,
            EOFError,
            ValueError,
            UnicodeEncodeError,
            NativeLifecycleError,
            json.JSONDecodeError,
        ):
            return None

    def _write_metadata(self) -> None:
        """Persist the controller endpoint metadata for a later process to discover."""
        assert self._metadata is not None
        temporary: Path | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=".instance-", suffix=".tmp", dir=self.directory
            )
            temporary = Path(temporary_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(self._metadata.encode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
            temporary = None
            try:
                os.chmod(self.state_path, 0o600)
            except OSError:
                pass
        except OSError:
            raise NativeLifecycleError("could not publish native instance state") from None
        finally:
            if temporary is not None:
                _safe_unlink(temporary)

    def close(self) -> None:
        owned = self.owns_instance
        self._closed.set()
        if self._listener is not None:
            self._listener.close()
        if self._listener_thread is not None:
            self._listener_thread.join(timeout=1)
        if self._metadata is not None and self._metadata.endpoint and os.name == "posix":
            _safe_unlink(Path(self._metadata.endpoint))
        if owned:
            _safe_unlink(self.state_path)
        self._release_lock()
