"""Standalone HTTP controller. Dedicated scanner workers are never its children.

Each HTTP generation connects through the frozen-program's private ``http-child``
role over a controller-owned authenticated local socket.  Workers remain separate
from HTTP generation ownership, so a normal HTTP restart preserves active scans.
"""

from __future__ import annotations

import base64
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import replace
from threading import Event, Lock, Thread, current_thread, main_thread

from scanner.models import RestartCapability
from scanner.runtime.process_control import (
    GRACEFUL_STOP_TIMEOUT,
    ProcessControl,
    cleanup_worker_control_endpoint,
    controller_control_path,
    create_private_listener,
)
from scanner.settings.deployment_config import DeploymentSnapshot, DeploymentStore

# Installed only inside a spawned child before scanner.api is imported. Recovery
# must not import the API through a second resolution of failed pending settings.
initial_snapshot: DeploymentSnapshot | None = None
STARTUP_TIMEOUT = 30
SHUTDOWN_TIMEOUT = 15
IPC_TIMEOUT = 2


class RestartClient:
    def __init__(self, connection, capability: RestartCapability):
        self.connection = connection
        self.capability = capability
        self.lock = Lock()
        self.server = None

    def reserve(self) -> str | None:
        """Reserve once with the parent before claiming an accepted restart."""
        with self.lock:
            if self.capability.in_progress:
                return "restart_in_progress"
            try:
                self.connection.send(("reserve",))
                if not self.connection.poll(IPC_TIMEOUT):
                    raise OSError("controller response timed out")
                if self.connection.recv() != ("reserved",):
                    raise OSError("invalid controller response")
            except (OSError, EOFError):
                self.capability.supported = False
                self.capability.reason = "controller_unavailable"
                self.capability.detail = "The standalone controller is unavailable"
                return "restart_unavailable"
            self.capability.in_progress = True
            self.capability.outcome = "restarting"
            self.capability.target_generation = self.capability.generation + 1
            return None

    def commit(self) -> None:
        """Called by response background work only after the final response body."""
        try:
            self.connection.send(("restart",))
        except (OSError, EOFError):
            self.capability.in_progress = False
            self.capability.outcome = "failed"
            self.capability.supported = False
            self.capability.reason = "controller_unavailable"
            self.capability.detail = "Restart could not reach the standalone controller"
            return
        self.server.should_exit = True

    def cancel(self) -> None:
        """Release a reservation when its acknowledgment cannot be delivered."""
        with self.lock:
            if not self.capability.in_progress:
                return
            try:
                self.connection.send(("cancel",))
            except (OSError, EOFError):
                pass
            self.capability.in_progress = False
            self.capability.outcome = "idle"
            self.capability.target_generation = None


class _ReadinessGate:
    """Keep requests outside application/auth until activation is committed."""

    def __init__(self, application):
        self.application = application
        self.ready = False

    async def __call__(self, scope, receive, send):
        """Handle one controller command using the current service generation."""
        if scope["type"] == "http" and not self.ready:
            await send(
                {"type": "http.response.start", "status": 503, "headers": [(b"retry-after", b"1")]}
            )
            await send({"type": "http.response.body", "body": b"Service starting"})
            return

        async def send_response(message):
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                commit = scope.pop("editarr.restart_after_response", None)
                if commit is not None:
                    scope.pop("editarr.restart_cancel_response", None)
                    commit()

        try:
            await self.application(scope, receive, send_response)
        except BaseException:
            cancel = scope.pop("editarr.restart_cancel_response", None)
            if cancel is not None:
                cancel()
            raise


def _recover_or_raise(last_working, recovering: bool):
    if last_working is not None and not recovering:
        return last_working
    if recovering:
        raise RuntimeError(
            "Restart failed and previous listener recovery also failed. "
            "Free the listener or correct deployment.json, then start Editarr again"
        )
    raise RuntimeError("Initial HTTP startup failed; inspect the preceding diagnostic")


def _activate_application(application, context, store, *, recovering: bool):
    # Queue reconciliation can fail (for example on an unavailable database).
    # Finish it before committing a durable cutoff for this URL, while the outer
    # readiness gate still blocks requests. A failed startup must leave the last
    # working session scope intact for listener recovery and later cold starts.
    if not application.state.setup_required:
        application.state.activate_scan_queue_processing()
    snapshot = context.snapshot if recovering else store.activate(context.snapshot)
    application.state.deployment_snapshot = snapshot
    application.state.launch_context = replace(context, snapshot=snapshot)
    return snapshot


def serve_http_child(connection, context, capability, recovering=False):
    """Run one HTTP generation and report its readiness to the controller."""
    global initial_snapshot
    initial_snapshot = context.snapshot
    listener = None
    try:
        # Bind before application startup can activate its queue scheduler. Keep
        # the bound socket open through Uvicorn startup so no other process can
        # claim it between preflight and activation.
        family = socket.AF_INET6 if ":" in context.host else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((context.host, context.snapshot.listening_port))
        import uvicorn

        from scanner.api import app, configure_http_service
        from scanner.settings.config import settings

        configure_http_service(app, context)
        client = RestartClient(connection, capability)
        app.state.restart_client = client
        app.state.defer_scan_queue_activation = True
        app.state.restart_capability = capability
        gate = _ReadinessGate(app)

        class Server(uvicorn.Server):
            async def startup(self, sockets=None):
                """Start the HTTP child and wait for its readiness before serving requests."""
                await super().startup(sockets=sockets)
                if self.should_exit or not self.started:
                    return
                try:
                    # Recovery deliberately retains the last working cutoff and
                    # pending metadata, including if the desired file is invalid.
                    snapshot = _activate_application(
                        app, context, DeploymentStore(settings.data_dir),
                        recovering=recovering,
                    )
                    connection.send(("ready", snapshot))
                    gate.ready = True
                except Exception:
                    await self.shutdown(sockets=sockets)
                    raise

        server = Server(
            uvicorn.Config(
                gate,
                host=context.host,
                port=context.snapshot.listening_port,
                proxy_headers=False,
                timeout_graceful_shutdown=10,
            )
        )
        client.server = server
        server.run(sockets=[listener])
    except BaseException as error:
        # Full local diagnostic goes to operator stderr, never a public response.
        print(f"HTTP generation failed: {error}", file=sys.stderr, flush=True)
        try:
            connection.send(("failed",))
        except (OSError, EOFError):
            pass
        raise SystemExit(1) from None
    finally:
        if listener is not None:
            listener.close()
        connection.close()


def _stop_child(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()  # PID only; never signal independent worker groups.
        try:
            process.wait(SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        process.kill()
        process.wait(5)


class _HttpChildEndpoint:
    """One authenticated controller socket, removed after its generation ends."""

    def __init__(self):
        self.path = controller_control_path()
        self.token = os.urandom(32)
        self.listener = create_private_listener(self.path, self.token)

    def accept(self, timeout: float):
        """Accept a private controller request and dispatch its authenticated command."""
        result: list[object] = []
        done = Event()

        def receive() -> None:
            try:
                result.append(self.listener.accept())
            except (EOFError, OSError) as error:
                result.append(error)
            finally:
                done.set()

        Thread(target=receive, name="editarr-http-accept", daemon=True).start()
        if not done.wait(timeout):
            raise TimeoutError("HTTP child did not connect to its controller")
        value = result[0]
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.listener.close()
        if hasattr(self.path, "unlink"):
            self.path.unlink(missing_ok=True)


def _launch_http_child(endpoint: _HttpChildEndpoint) -> subprocess.Popen[bytes]:
    from scanner.entry import http_child_command

    environment = os.environ.copy()
    encoded_token = base64.urlsafe_b64encode(endpoint.token).decode("ascii")
    environment["EDITARR_INTERNAL_CONTROLLER_TOKEN"] = encoded_token
    return subprocess.Popen(http_child_command(str(endpoint.path)), env=environment)


def _full_quit_owned_workers() -> None:
    """Request worker shutdown, then force only identities that prove ownership."""

    from scanner.db import get_session_factory
    from scanner.persistence.migrations.preflight import require_supported_schema
    from scanner.repository import mark_scan_orphaned, scanning_scan_worker_processes

    control = ProcessControl()
    db = None
    try:
        require_supported_schema()
        db = get_session_factory()()
        for scan_id, pid, pgid, token in scanning_scan_worker_processes(db):
            identity = control.identity(scan_id, pid, pgid, token)
            if identity is None:
                cleanup_worker_control_endpoint(scan_id, token)
                mark_scan_orphaned(
                    db, scan_id, "Interrupted by application Quit — ready to resume"
                )
                continue
            control.request_stop(identity)
            if not control.wait_for_exit(identity, GRACEFUL_STOP_TIMEOUT):
                control.force_stop(identity)
            cleanup_worker_control_endpoint(scan_id, token)
            mark_scan_orphaned(db, scan_id, "Interrupted by application Quit — ready to resume")
    except Exception:
        # A failed cold start can occur before schema initialization.  Preserve its
        # primary diagnostic rather than replacing it with a best-effort Quit query.
        return
    finally:
        if db is not None:
            db.close()


def run_controller(context, *, on_ready=None, stop_event: Event | None = None):
    """Own one HTTP generation; recover once on a requested startup failure."""
    from scanner.cli import _build_parser, resolve_server_launch_context

    stopping = False
    process = None
    connection = None
    endpoint = None
    old_handlers = {}

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    if current_thread() is main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig, stop)
    generation = 1
    outcome = "idle"
    last_working = None
    recovering = False
    detail = None
    try:
        while not stopping:
            endpoint = _HttpChildEndpoint()
            capability = RestartCapability(
                supported=True,
                mode="standalone_controller",
                generation=generation,
                in_progress=False,
                outcome=outcome,
                target_generation=generation if generation > 1 else None,
                detail=detail,
            )
            process = _launch_http_child(endpoint)
            connection = endpoint.accept(STARTUP_TIMEOUT)
            connection.send(
                (
                    "start",
                    {
                        "host": context.host,
                        "cli_port_override": context.cli_port_override,
                        "api_only": context.api_only,
                        "snapshot": context.snapshot,
                        "original_argv": context.original_argv,
                    },
                    capability.model_dump(),
                    recovering,
                )
            )
            ready = False
            readiness_timed_out = False
            requested = False
            reserved = False
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while not stopping:
                if stop_event is not None and stop_event.is_set():
                    stopping = True
                    break
                if connection.poll(0.1):
                    try:
                        message = connection.recv()
                    except EOFError:
                        break
                    if message[0] == "ready":
                        ready = True
                        context = replace(context, snapshot=message[1])
                        last_working = context
                        if on_ready is not None:
                            on_ready(context.snapshot)
                    elif message[0] == "reserve" and ready and not reserved:
                        reserved = True
                        connection.send(("reserved",))
                    elif message[0] == "restart" and reserved:
                        requested = True
                        break
                    elif message[0] == "cancel" and reserved:
                        reserved = False
                    elif message[0] == "failed":
                        break
                if process.poll() is not None:
                    break
                if not ready and time.monotonic() > deadline:
                    readiness_timed_out = True
                    break
            if stopping:
                break
            if requested:
                # Child has completed its acknowledgment and requested shutdown.
                _stop_child(process)
                connection.close()
                endpoint.close()
                generation += 1
                recovering = False
                outcome, detail = "applied", None
                try:
                    context = resolve_server_launch_context(
                        _build_parser().parse_args(context.original_argv),
                        original_argv=context.original_argv,
                    )
                except Exception as error:
                    print(f"Restart configuration failed: {error}", file=sys.stderr, flush=True)
                    context = last_working
                    recovering = True
                    outcome = "recovered"
                    detail = (
                        "New configuration failed; previous listener restored. "
                        "Saved changes remain pending. Correct deployment settings and retry."
                    )
                continue
            _stop_child(process)
            connection.close()
            endpoint.close()
            if ready:
                # Unexpected HTTP exit is not a requested restart. No automatic
                # supervisor loop or worker ownership is introduced here.
                raise RuntimeError("HTTP service exited unexpectedly; start Editarr again")
            if readiness_timed_out and last_working is None:
                raise RuntimeError(
                    "HTTP readiness timed out; inspect the preceding diagnostic and "
                    "start Editarr again"
                )
            if last_working is not None and not recovering:
                context = _recover_or_raise(last_working, recovering)
                recovering = True
                outcome = "recovered"
                detail = (
                    "New listener or startup failed; previous listener restored. "
                    "Saved changes remain pending. Correct deployment settings and retry."
                )
                print(detail, file=sys.stderr, flush=True)
                continue
            _recover_or_raise(last_working, recovering)
    finally:
        if process is not None:
            _stop_child(process)
        if connection is not None:
            connection.close()
        if endpoint is not None:
            endpoint.close()
        _full_quit_owned_workers()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
