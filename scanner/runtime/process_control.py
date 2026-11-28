"""Cross-platform ownership and control for dedicated Editarr workers.

The persisted scan row deliberately stores only a PID, a POSIX process-group
identifier, and an opaque worker token.  A PID is *not* an ownership proof: it
can be reused after a crash. POSIX uses private Unix-domain endpoints; Windows
uses private same-user-ACL named pipes. Both require a token-authenticated
challenge-response before reporting a worker as live or sending it a signal.

Windows worker descendants are also assigned to a named Job Object. The job is
intentionally not ``KILL_ON_JOB_CLOSE``: an HTTP-generation restart closes the
launching child but must preserve a scan. The controller can reopen the named
job during a full application Quit.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import signal
import socket
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Any

from scanner.runtime.process_control_windows import (
    _JOB_OBJECT_QUERY as _JOB_OBJECT_QUERY,
)
from scanner.runtime.process_control_windows import (
    _JOB_OBJECT_TERMINATE as _JOB_OBJECT_TERMINATE,
)
from scanner.runtime.process_control_windows import (
    CONTROL_TIMEOUT,
    ProcessControlError,
    _assign_windows_job,
    _connect_windows_pipe,
    _terminate_windows_job,
    _verify_windows_worker_job,
    _WindowsJobHandle,
    _WindowsPipeListener,
    windows_worker_may_be_running,
)
from scanner.runtime.process_control_windows import (
    _windows_api as _windows_api,
)
from scanner.runtime.process_control_windows import (
    _windows_logon_security_attributes as _windows_logon_security_attributes,
)
from scanner.runtime.process_control_windows import (
    _windows_logon_sid as _windows_logon_sid,
)
from scanner.runtime.process_control_windows import (
    _windows_pipe_mode as _windows_pipe_mode,
)
from scanner.runtime.process_control_windows import (
    _WindowsApi as _WindowsApi,
)
from scanner.runtime.process_control_windows import (
    _WindowsPipeConnection as _WindowsPipeConnection,
)
from scanner.runtime.runtime_paths import ensure_user_private_directory, get_runtime_paths

from .worker_checkpoint_lease import checkpoint_writer_may_be_running

# The endpoint is ready before pipeline startup, including in frozen builds.
WORKER_START_TIMEOUT = 30.0
GRACEFUL_STOP_TIMEOUT = 10.0
FORCE_STOP_TIMEOUT = 3.0

@dataclass(frozen=True, slots=True)
class WorkerIdentity:
    """The persisted values and derived endpoint for one worker."""

    scan_id: str
    pid: int
    process_group: int | None
    token: str


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


def control_transport(platform_name: str | None = None) -> str:
    """Return the contract-selected private IPC transport for one platform."""

    name = os.name if platform_name is None else platform_name
    return "windows-named-pipe" if name == "nt" else "unix-domain-socket"


def worker_control_path(
    scan_id: str, token: str, *, platform_name: str | None = None
) -> Path | str:
    """Return the private endpoint without exposing ``token``."""

    if control_transport(platform_name) == "windows-named-pipe":
        return rf"\\.\pipe\EditarrWorker-{scan_id[:8]}-{_token_digest(token)}"

    paths = get_runtime_paths()
    root = _socket_root(paths.temp_dir, "editarr-worker-control")
    # AF_UNIX path limits are small on macOS. UUID + digest keeps this predictable.
    return root / f"w-{scan_id[:8]}-{_token_digest(token)}.sock"


def cleanup_worker_control_endpoint(scan_id: str, token: str | None) -> None:
    """Remove only this dead worker's derived POSIX control socket.

    A force-killed or crashed worker cannot execute ``WorkerControlServer.close``.
    Reconciliation has its scan id and opaque token, so it can remove that one
    endpoint without sweeping the shared per-user IPC directory.  Refuse to unlink
    any other file type even though the directory itself is owner-private.
    """

    if os.name != "posix" or not token:
        return
    path = worker_control_path(scan_id, token)
    if not isinstance(path, Path):  # pragma: no cover - guarded by POSIX above
        return
    try:
        if stat.S_ISSOCK(path.lstat().st_mode):
            path.unlink()
    except FileNotFoundError:
        return


def controller_control_path() -> Path | str:
    """Allocate a private controller endpoint for one HTTP generation."""

    if os.name == "nt":
        return rf"\\.\pipe\EditarrHttp-{secrets.token_hex(16)}"
    root = _socket_root(get_runtime_paths().temp_dir, "editarr-controller")
    return root / f"http-{secrets.token_hex(16)}.sock"


def _socket_root(temp_dir: Path, name: str) -> Path:
    """Use the runtime temp root unless an AF_UNIX kernel path would overflow."""

    candidate = temp_dir / name
    # macOS permits 104 bytes for a socket pathname.  Keep a margin for the
    # endpoint name.  The fallback is still an owner-private OS temp directory.
    if os.name == "posix" and len(os.fsencode(str(candidate))) > 55:
        # TMPDIR itself is often a long per-user macOS path.  AF_UNIX has a
        # fixed kernel limit, so use a unique owner-private directory directly
        # beneath the OS temporary root only for the socket namespace.
        candidate = Path("/tmp") / f"editarr-ipc-{os.getuid()}" / name
    return ensure_user_private_directory(candidate)


def worker_job_name(scan_id: str, token: str) -> str:
    """Return the Windows named Job Object for this worker without exposing its token."""

    return f"Local\\EditarrWorker-{scan_id[:8]}-{_token_digest(token)}"


def _proof(token: str, challenge: bytes, pid: int) -> bytes:
    return hmac.new(
        token.encode("utf-8"), challenge + b":" + str(pid).encode("ascii"), hashlib.sha256
    ).digest()


def create_private_listener(address: Path | str, token: bytes):
    if os.name == "nt":
        return _WindowsPipeListener(str(address), token)
    from multiprocessing.connection import Listener

    return Listener(str(address), family="AF_UNIX", authkey=token)


def connect_private_endpoint(address: Path | str, token: bytes):
    if os.name == "nt":
        return _connect_windows_pipe(str(address), token)
    from multiprocessing.connection import Client

    return Client(str(address), family="AF_UNIX", authkey=token)


class WorkerControlServer:
    """A worker-owned control endpoint for identity proof and cooperative Quit."""

    def __init__(
        self,
        scan_id: str,
        token: str,
        stop_event: Event,
    ):
        """Create a private worker control endpoint with its stop signal."""
        self.scan_id = scan_id
        self.path = worker_control_path(scan_id, token)
        self.token = token
        self.stop_event = stop_event
        self._closed = Event()
        self._socket: socket.socket | None = None
        self._pipe_listener = None
        self._thread: Thread | None = None

    def start(self) -> None:
        """Bind the private endpoint and start its background command listener."""
        if os.name == "nt":
            self._pipe_listener = create_private_listener(self.path, self.token.encode("utf-8"))
            # Do this before returning from start().  Permanent Windows security or
            # pipe-creation errors must fail the worker process visibly instead of
            # becoming an indistinguishable controller identity timeout.
            self._pipe_listener.prepare()
            self._thread = Thread(
                target=self._serve_pipe, name="editarr-worker-control", daemon=True
            )
            self._thread.start()
            return
        if not hasattr(socket, "AF_UNIX"):
            raise ProcessControlError("worker control sockets are unavailable on this platform")
        try:
            self.path.unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            listener.listen(4)
            listener.settimeout(0.2)
        except OSError as error:
            raise ProcessControlError("could not create private worker control endpoint") from error
        self._socket = listener
        self._thread = Thread(target=self._serve, name="editarr-worker-control", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        """Accept POSIX socket clients and answer authenticated worker commands."""
        assert self._socket is not None
        while not self._closed.is_set():
            try:
                client, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with client:
                client.settimeout(CONTROL_TIMEOUT)
                try:
                    payload = client.recv(512)
                    response = self._handle_payload(payload)
                    if response is not None:
                        client.sendall(response)
                except (OSError, ValueError):
                    continue

    def _serve_pipe(self) -> None:
        """Accept Windows pipe clients and answer authenticated worker commands."""
        assert self._pipe_listener is not None
        while not self._closed.is_set():
            try:
                client = self._pipe_listener.accept()
            except ProcessControlError:
                self.stop_event.set()
                return
            except (OSError, PermissionError):
                # A client may disconnect or fail authentication; preserve service
                # availability and wait for the next authenticated client.
                if self._closed.is_set():
                    return
                continue
            try:
                response = self._handle_payload(client.recv_bytes())
                if response is not None:
                    client.send_bytes(response)
            except (EOFError, OSError, ValueError):
                pass
            finally:
                client.close_server()

    def _handle_payload(self, payload: bytes) -> bytes | None:
        """Verify a command proof before reporting identity or requesting stop."""
        command, encoded, supplied = payload.split(b" ", 2)
        challenge = base64.urlsafe_b64decode(encoded + b"===")
        supplied_proof = base64.urlsafe_b64decode(supplied.strip() + b"===")
        expected = hmac.new(
            self.token.encode("utf-8"), command + b":" + challenge, hashlib.sha256
        ).digest()
        if not challenge or not hmac.compare_digest(supplied_proof, expected):
            return None
        if command == b"proof":
            return (
                b"proof "
                + str(os.getpid()).encode("ascii")
                + b" "
                + base64.urlsafe_b64encode(_proof(self.token, challenge, os.getpid()))
            )
        if command == b"stop":
            self.stop_event.set()
            return b"stopping"
        return None

    def close(self) -> None:
        self._closed.set()
        # Unlink before closing the listener. A controller treats connection
        # failure as worker exit, so it must never observe that transition while a
        # kill can still strand this socket pathname.
        cleanup_worker_control_endpoint(self.scan_id, self.token)
        if self._socket is not None:
            self._socket.close()
        if self._pipe_listener is not None:
            self._pipe_listener.close()
        if self._thread is not None:
            self._thread.join(timeout=1)


def _control_payload(identity: WorkerIdentity, command: bytes, challenge: bytes) -> bytes:
    request_proof = hmac.new(
        identity.token.encode("utf-8"), command + b":" + challenge, hashlib.sha256
    ).digest()
    return (
        command
        + b" "
        + base64.urlsafe_b64encode(challenge)
        + b" "
        + base64.urlsafe_b64encode(request_proof)
    )


def _request(
    identity: WorkerIdentity, command: bytes, challenge: bytes | None = None
) -> bytes | None:
    """Send an authenticated command to a worker endpoint and read its response."""
    challenge = secrets.token_bytes(24) if challenge is None else challenge
    payload = _control_payload(identity, command, challenge)
    try:
        if os.name == "nt":
            client = connect_private_endpoint(
                worker_control_path(identity.scan_id, identity.token),
                identity.token.encode("utf-8"),
            )
            try:
                client.send_bytes(payload)
                return client.recv_bytes()
            finally:
                client.close()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(CONTROL_TIMEOUT)
            client.connect(str(worker_control_path(identity.scan_id, identity.token)))
            client.sendall(payload)
            return client.recv(512)
    except (OSError, ValueError):
        return None


def _authenticated_worker_pid(scan_id: str, token: str) -> int | None:
    """Return the PID reported by a token-authenticated worker endpoint."""

    challenge = secrets.token_bytes(24)
    response = _request(WorkerIdentity(scan_id, 0, None, token), b"proof", challenge)
    if response is None:
        return None
    parts = response.split(b" ")
    if len(parts) != 3 or parts[0] != b"proof":
        return None
    try:
        response_pid = int(parts[1])
        actual = base64.urlsafe_b64decode(parts[2] + b"===")
    except ValueError:
        return None
    if response_pid <= 0 or not hmac.compare_digest(actual, _proof(token, challenge, response_pid)):
        return None
    return response_pid


def _prove_identity(identity: WorkerIdentity) -> bool:
    """Require a fresh authenticated endpoint response for this persisted PID."""

    return _authenticated_worker_pid(identity.scan_id, identity.token) == identity.pid


class ProcessControl:
    """The only worker lifecycle adapter used by API/controller code."""

    def launch_worker(
        self,
        command: list[str],
        scan_id: str,
        token: str,
        *,
        environment: dict[str, str] | None = None,
    ) -> subprocess.Popen[bytes]:
        """Launch a dedicated worker with platform isolation and supplied environment."""
        kwargs: dict[str, object] = {}
        if os.name == "posix":
            # Encapsulated here so callers never depend on a POSIX session API.
            kwargs["start_new_session"] = True
        # A Windows source command can begin as a short-lived venv redirector.
        # The actual worker interpreter joins its derived Job before it exposes
        # the authenticated endpoint; assigning this transient Popen PID races
        # that hand-off and is not an ownership proof.
        return subprocess.Popen(command, env=environment, **kwargs)

    def identity(
        self, scan_id: str, pid: int | None, process_group: int | None, token: str | None
    ) -> WorkerIdentity | None:
        if pid is None or token is None:
            return None
        identity = WorkerIdentity(scan_id, pid, process_group, token)
        return identity if _prove_identity(identity) else None

    def discover_identity(
        self,
        scan_id: str,
        token: str,
        *,
        launcher_pid: int,
        process_group: int | None = None,
        platform_name: str | None = None,
        launcher_process: subprocess.Popen[bytes] | None = None,
    ) -> WorkerIdentity | None:
        """Discover and bind the worker-reported PID during launch registration.

        Windows source launches can first execute a short-lived virtualenv
        redirector.  Its Popen PID is not persisted: only the endpoint's
        token-authenticated interpreter PID becomes durable identity. Later
        lifecycle calls still require that exact persisted PID.
        """

        name = os.name if platform_name is None else platform_name
        deadline = time.monotonic() + WORKER_START_TIMEOUT
        while time.monotonic() < deadline:
            pid = _authenticated_worker_pid(scan_id, token)
            if pid is not None:
                # Only the Windows venv redirector can legitimately differ. On
                # POSIX, accepting a token-valid PID other than the launcher would
                # weaken the PID-reuse boundary.
                if name != "nt" and pid != launcher_pid:
                    return None
                return WorkerIdentity(scan_id, pid, process_group, token)
            # POSIX launches keep the same PID. Reap an exited child promptly and
            # preserve the failure instead of spending the whole cold-start grace
            # on it. A Windows venv redirector may exit before its worker is ready.
            if name != "nt" and launcher_process is not None:
                returncode = launcher_process.poll()
                if returncode is not None:
                    raise ProcessControlError(
                        "Worker exited before establishing a token-authenticated "
                        f"identity (exit code {returncode})"
                    )
            time.sleep(0.05)
        return None

    def ensure_worker_job(self, identity: WorkerIdentity) -> None:
        """Verify the proven Windows interpreter is in its derived Job Object."""

        if os.name == "nt":
            _verify_windows_worker_job(
                identity.pid, worker_job_name(identity.scan_id, identity.token)
            )

    def request_stop(self, identity: WorkerIdentity) -> bool:
        if not _prove_identity(identity):
            return False
        response = _request(identity, b"stop")
        return response == b"stopping"

    def worker_may_be_running(self, pid: int | None, process_group: int | None) -> bool:
        """Require more than an IPC timeout before declaring a paused worker gone."""
        if pid is None or pid <= 0:
            return False
        if os.name == "nt":
            return windows_worker_may_be_running(pid)
        try:
            # A subprocess started by this HTTP process may still be a zombie.
            reaped, _status = os.waitpid(pid, os.WNOHANG)
            if reaped:
                return False
        except ChildProcessError:
            pass
        try:
            group = os.getpgid(pid)
            return process_group is None or group == process_group
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def attempt_may_be_running(
        self, scan_id: str, pid: int | None, pgid: int | None, token: str | None,
        *, data_dir: Path,
    ) -> bool:
        """Keep an attempt reserved while its worker or detached checkpoint writer is live."""
        return self.worker_may_be_running(pid, pgid) or checkpoint_writer_may_be_running(
            data_dir, scan_id, token
        )

    def wait_for_exit(self, identity: WorkerIdentity, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not _prove_identity(identity):
                return True
            time.sleep(0.05)
        return not _prove_identity(identity)

    def force_stop(self, identity: WorkerIdentity) -> bool:
        """Terminate only a currently proven worker and its owned descendants."""

        if not _prove_identity(identity):
            return False
        if os.name == "nt":
            return _terminate_windows_job(worker_job_name(identity.scan_id, identity.token))
        if not _proven_posix_group(identity):
            return False
        try:
            os.killpg(identity.process_group, signal.SIGTERM)  # type: ignore[arg-type]
        except ProcessLookupError:
            return False
        deadline = time.monotonic() + FORCE_STOP_TIMEOUT
        while time.monotonic() < deadline:
            if not _prove_identity(identity):
                cleanup_worker_control_endpoint(identity.scan_id, identity.token)
                return True
            time.sleep(0.05)
        # Re-prove PID/token/group immediately before escalation. A stopped worker
        # with a recycled PID or group is never sent SIGKILL.
        if not _prove_identity(identity) or not _proven_posix_group(identity):
            cleanup_worker_control_endpoint(identity.scan_id, identity.token)
            return True
        try:
            os.killpg(identity.process_group, signal.SIGKILL)  # type: ignore[arg-type]
        except ProcessLookupError:
            cleanup_worker_control_endpoint(identity.scan_id, identity.token)
            return True
        cleanup_worker_control_endpoint(identity.scan_id, identity.token)
        return True


def _proven_posix_group(identity: WorkerIdentity) -> bool:
    if identity.process_group is None or identity.process_group <= 0:
        return False
    try:
        return (
            os.getpgid(identity.pid) == identity.process_group
            and identity.process_group != os.getpgrp()
        )
    except ProcessLookupError:
        return False


def enter_isolated_visual_worker() -> None:
    """Detach a short-lived POSIX visual worker inside the lifecycle adapter."""

    if os.name != "posix":
        return
    try:
        os.setsid()
    except OSError:
        return


def terminate_isolated_process(process: Any) -> None:
    """Stop a multiprocessing scan helper and its POSIX descendants safely."""

    process_group: int | None = None
    if process.pid and os.name == "posix":
        try:
            candidate = os.getpgid(process.pid)
        except ProcessLookupError:
            candidate = None
        if candidate is not None and candidate != os.getpgrp():
            process_group = candidate
    if process_group is not None:
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    process.join(timeout=2)
    if process.is_alive():
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        process.join()


def assign_current_worker_job(scan_id: str, token: str) -> _WindowsJobHandle | None:
    """Join and retain the actual interpreter's Windows Job for its lifetime."""

    if os.name == "nt":
        return _assign_windows_job(os.getpid(), worker_job_name(scan_id, token))
    return None
