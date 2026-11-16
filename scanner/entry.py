"""Minimal executable bootstrap for source and frozen Editarr launches.

This module intentionally imports only the standard library and ``runtime_paths``
before diverting multiprocessing or private child roles.  A freezer should use
``scanner.entry:main`` as its application entry point and collect the required
resources into the frozen package.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import uuid
from collections.abc import Callable, Sequence

from scanner.runtime.runtime_paths import (
    bootstrap_entrypoint_runtime_paths,
    is_frozen_application,
)

INTERNAL_ROLE_OPTION = "--internal-role"
HTTP_CHILD_ROLE = "http-child"
SCAN_WORKER_ROLE = "scan-worker"
INTERNAL_ROLES = frozenset((HTTP_CHILD_ROLE, SCAN_WORKER_ROLE))


class InternalRoleError(ValueError):
    """A private invocation is malformed or cannot run in this bootstrap."""


def scan_worker_command(
    scan_id: str,
    worker_token: str,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    """Return the source or frozen command for one dedicated scan worker.

    Tokens remain individual argv values, never shell text.  The source form is
    retained for developer tools and tests; a frozen application relaunches itself
    through its private role instead of asking users to have a Python interpreter.
    """

    program = sys.executable if executable is None else executable
    frozen_mode = is_frozen_application() if frozen is None else frozen
    if frozen_mode:
        return [
            program,
            INTERNAL_ROLE_OPTION,
            SCAN_WORKER_ROLE,
            "--scan-id",
            scan_id,
            "--worker-token",
            worker_token,
        ]
    return [
        program,
        "-m",
        "scanner.worker",
        "--scan-id",
        scan_id,
        "--worker-token",
        worker_token,
    ]


def http_child_command(
    controller_socket: str, *, executable: str | None = None, frozen: bool | None = None
) -> list[str]:
    """Return the private controller-to-HTTP-generation command."""

    program = sys.executable if executable is None else executable
    frozen_mode = is_frozen_application() if frozen is None else frozen
    prefix = [program] if frozen_mode else [program, "-m", "scanner.entry"]
    return [
        *prefix,
        INTERNAL_ROLE_OPTION,
        HTTP_CHILD_ROLE,
        "--controller-socket",
        controller_socket,
    ]


def _private_role_arguments(argv: Sequence[str]) -> tuple[str, tuple[str, ...]] | None:
    """Extract one hidden role without invoking the public argparse parser."""

    values: list[tuple[int, str]] = []
    for index, argument in enumerate(argv):
        if argument == INTERNAL_ROLE_OPTION:
            if index + 1 >= len(argv):
                raise InternalRoleError
            values.append((index, argv[index + 1]))
        elif argument.startswith(f"{INTERNAL_ROLE_OPTION}="):
            values.append((index, argument.split("=", maxsplit=1)[1]))
    if not values:
        return None
    if len(values) != 1:
        raise InternalRoleError
    index, role = values[0]
    if role not in INTERNAL_ROLES:
        raise InternalRoleError
    if argv[index] == INTERNAL_ROLE_OPTION:
        remaining = tuple((*argv[:index], *argv[index + 2 :]))
    else:
        remaining = tuple((*argv[:index], *argv[index + 1 :]))
    return role, remaining


def _value_for_option(arguments: Sequence[str], option: str) -> str:
    """Read one private option while rejecting duplicate/malformed forms."""

    values: list[str] = []
    for index, argument in enumerate(arguments):
        if argument == option:
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("-"):
                raise InternalRoleError
            values.append(arguments[index + 1])
        elif argument.startswith(f"{option}="):
            values.append(argument.split("=", maxsplit=1)[1])
    if len(values) != 1 or not values[0]:
        raise InternalRoleError
    return values[0]


def _scan_worker_arguments(arguments: Sequence[str]) -> tuple[str, str]:
    """Validate the exact private scan-worker argument shape."""

    if len(arguments) != 4:
        raise InternalRoleError
    scan_id = _value_for_option(arguments, "--scan-id")
    worker_token = _value_for_option(arguments, "--worker-token")
    if set(arguments[::2]) != {"--scan-id", "--worker-token"}:
        raise InternalRoleError
    try:
        uuid.UUID(scan_id)
    except (AttributeError, ValueError):
        raise InternalRoleError from None
    return scan_id, worker_token


def _run_scan_worker_role(scan_id: str, worker_token: str) -> int:
    """Import worker implementation only after private validation succeeds."""

    from scanner.worker import run_scan_worker

    return run_scan_worker(scan_id, worker_token)


def _http_child_arguments(arguments: Sequence[str]) -> str:
    if len(arguments) != 2 or arguments[0] != "--controller-socket" or not arguments[1]:
        raise InternalRoleError
    return arguments[1]


def _run_http_child_role(controller_socket: str) -> int:
    """Connect a private HTTP generation to its controller-owned IPC endpoint."""

    encoded_token = os.environ.get("EDITARR_INTERNAL_CONTROLLER_TOKEN")
    if not encoded_token:
        raise InternalRoleError
    try:
        import base64

        token = base64.urlsafe_b64decode(encoded_token.encode("ascii") + b"===")
    except (ValueError, UnicodeEncodeError):
        raise InternalRoleError from None
    if not token:
        raise InternalRoleError
    try:
        from scanner.runtime.process_control import connect_private_endpoint

        connection = connect_private_endpoint(controller_socket, token)
        # Register the controller context's stable pickle type before receiving it.
        from scanner.cli import ServerLaunchContext

        message = connection.recv()
    except (EOFError, OSError):
        raise InternalRoleError from None
    if not isinstance(message, tuple) or len(message) != 4 or message[0] != "start":
        connection.close()
        raise InternalRoleError
    _start, context_values, capability_values, recovering = message
    if not isinstance(context_values, dict) or not isinstance(capability_values, dict):
        connection.close()
        raise InternalRoleError
    try:
        context = ServerLaunchContext(**context_values)
        from scanner.models import RestartCapability
        from scanner.runtime.runtime_logging import (
            configure_runtime_logging,
            redirect_standard_streams,
        )
        from scanner.runtime.runtime_paths import is_frozen_application
        from scanner.settings.config import settings
        from scanner.settings.diagnostics import debug_logging_enabled

        capability = RestartCapability(**capability_values)
    except (TypeError, ValueError):
        connection.close()
        raise InternalRoleError from None
    native_launcher = (
        is_frozen_application() or os.environ.get("EDITARR_NATIVE_LAUNCHER") == "1"
    ) and os.environ.get("EDITARR_NATIVE_FOREGROUND") != "1"
    configure_runtime_logging(
        settings.log_dir,
        process_role="http" if native_launcher else None,
        debug_enabled=debug_logging_enabled(settings.data_dir),
    )
    if native_launcher:
        redirect_standard_streams(settings.log_dir, "http")
    from scanner.runtime.restart_service import serve_http_child

    return serve_http_child(connection, context, capability, recovering=bool(recovering))


def dispatch_internal_role(
    argv: Sequence[str],
    *,
    scan_runner: Callable[[str, str], int] = _run_scan_worker_role,
    http_child_runner: Callable[[str], int] = _run_http_child_role,
) -> int | None:
    """Run a private role, or return ``None`` for a normal public launch."""

    parsed = _private_role_arguments(argv)
    if parsed is None:
        return None
    role, arguments = parsed
    if role == SCAN_WORKER_ROLE:
        scan_id, worker_token = _scan_worker_arguments(arguments)
        return scan_runner(scan_id, worker_token)
    if role == HTTP_CHILD_ROLE:
        return http_child_runner(_http_child_arguments(arguments))
    raise InternalRoleError


def main(argv: Sequence[str] | None = None) -> None:
    """Run the executable bootstrap before public CLI or heavyweight imports."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    # This recognizes frozen ``--multiprocessing-fork`` arguments before they can
    # reach the public parser.  It may transfer control to a spawned target.
    multiprocessing.freeze_support()
    bootstrap_entrypoint_runtime_paths(list(arguments))
    try:
        private_exit_code = dispatch_internal_role(arguments)
    except InternalRoleError:
        # Do not echo argv: a worker token is intentionally opaque/private.
        print("error: invalid internal Editarr invocation", file=sys.stderr)
        raise SystemExit(2) from None
    if private_exit_code is not None:
        if private_exit_code:
            raise SystemExit(private_exit_code)
        return

    # This is intentionally environment-gated rather than a public operator flag.
    # Native build verification invokes the frozen executable itself and needs to
    # exercise imports which are otherwise lazy until a scan. It does not create
    # state, acquire a model, bind a socket, or open a browser.
    if os.environ.get("EDITARR_FROZEN_RUNTIME_SMOKE") == "1":
        from scanner.runtime.runtime_smoke import run_runtime_smoke

        raise SystemExit(run_runtime_smoke())

    # Delay the public parser and all application imports until child diversion.
    from scanner.cli import main as public_cli_main

    public_cli_main()


if __name__ == "__main__":
    main()
