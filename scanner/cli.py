"""Editarr service command-line entry point."""

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from scanner.runtime.runtime_paths import (
    bootstrap_entrypoint_runtime_paths,
    get_runtime_paths,
    is_frozen_application,
)

# This must remain before imports that construct scanner.settings.config.settings.  It is a
# deliberately tiny parse of only --data-dir; argparse owns all public CLI validation.
bootstrap_entrypoint_runtime_paths()

from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from scanner.commands.admin import AdminCommandError, run_admin_command  # noqa: E402
from scanner.runtime.runtime_logging import configure_runtime_logging  # noqa: E402
from scanner.settings.config import settings  # noqa: E402
from scanner.settings.deployment_config import (  # noqa: E402
    DeploymentConfigurationError,
    DeploymentConfigurationUnavailable,
    DeploymentSnapshot,
    DeploymentStore,
    resolve_deployment_snapshot,
)
from scanner.settings.diagnostics import debug_logging_enabled  # noqa: E402


class _CommandError(RuntimeError):
    """A concise, secret-free CLI failure suitable for an operator."""


def _desktop_browser_available(
    *, platform_name: str | None = None, environment: dict[str, str] | None = None
) -> bool:
    """Return whether a native launch has a desktop target for browser activation."""

    current_platform = sys.platform if platform_name is None else platform_name
    values = os.environ if environment is None else environment
    if current_platform.startswith("linux"):
        return bool(values.get("DISPLAY") or values.get("WAYLAND_DISPLAY"))
    return True


def _print_ready_url(snapshot: DeploymentSnapshot) -> None:
    print(f"Editarr is ready at {snapshot.base_url.rstrip('/')}/", flush=True)


@dataclass(frozen=True, slots=True)
class ServerLaunchContext:
    """Inputs the restart controller must retain for the current service generation.

    The restart controller uses this context to start a replacement HTTP child. It
    retains the *presence* of ``--port`` separately from the resolved port, so an
    operator's recovery override remains effective after a restart.
    """

    host: str
    cli_port_override: int | None
    api_only: bool
    snapshot: DeploymentSnapshot
    original_argv: tuple[str, ...]


# The controller may be launched with ``python -m scanner.cli``.  Its private
# HTTP child receives this context over authenticated IPC, so make its pickle
# identity stable rather than the transient ``__main__`` module name.
ServerLaunchContext.__module__ = "scanner.cli"


def _export_id(value: str) -> int:
    """A publication ID: a positive SQLite integer."""
    try:
        number = int(value)
    except ValueError:
        number = 0
    if not 0 < number < 2**63:
        raise argparse.ArgumentTypeError("must be a positive publication ID")
    return number


def _add_password_input(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from standard input (for non-interactive automation)",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Define the supported command groups and their command-line arguments."""
    parser = argparse.ArgumentParser(description="Editarr — Jellyfin-backed media editing service")
    parser.add_argument("--host", default="127.0.0.1", help="Address to bind")
    parser.add_argument(
        "--data-dir",
        help="Absolute durable-data directory (overrides EDITARR_DATA_DIR for this launch)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to bind temporarily (overrides saved deployment port)",
    )
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Run only the API; intended for development and installations without bundled assets",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the UI in the default browser after a native launch",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run diagnostic source/console service mode without native singleton control",
    )
    commands = parser.add_subparsers(dest="command")

    admin = commands.add_parser("admin", help="Owner status and committed-export writes")
    admin_commands = admin.add_subparsers(dest="admin_command", required=True)
    admin_commands.add_parser("status", help="Show whether an owner is configured")

    write_export = admin_commands.add_parser(
        "write-export",
        help="Write a publication's committed bytes to the path it records (owner's Jellyfin "
        "password)",
    )
    write_export.add_argument("export_id", type=_export_id, help="Publication (export) ID")
    _add_password_input(write_export)

    commands.add_parser("paths", help="Print resolved non-secret runtime locations")
    lifecycle = commands.add_parser(
        "lifecycle",
        help="Control the current per-user native application instance",
    )
    lifecycle.add_argument(
        "action",
        choices=("status", "open-ui", "open-logs", "quit"),
        help="Authenticated local lifecycle action",
    )
    migration = commands.add_parser(
        "migration", help="Inspect, upgrade, or export database recovery"
    )
    migration_commands = migration.add_subparsers(dest="migration_action", required=True)
    migration_commands.add_parser("inspect", help="Check schema compatibility without upgrading")
    migration_commands.add_parser("upgrade", help="Safely upgrade a known database")
    recovery = migration_commands.add_parser(
        "recover-export", help="Export a verified prior generation"
    )
    recovery.add_argument("destination", help="New file in a separate recovery data directory")
    backup = commands.add_parser(
        "backup", help="Export, inspect, restore or recover a portable backup"
    )
    backup_commands = backup.add_subparsers(dest="backup_action", required=True)
    export = backup_commands.add_parser("export", help="Export a full backup")
    export.add_argument("destination")
    export.add_argument("--encrypt", action="store_true", help="Encrypt with an age passphrase")
    _add_password_input(export)
    inspect = backup_commands.add_parser("inspect", help="Validate and preview a backup")
    inspect.add_argument("archive")
    _add_password_input(inspect)
    restore = backup_commands.add_parser(
        "restore", help="Restore a backup into an empty data directory"
    )
    restore.add_argument("archive")
    _add_password_input(restore)
    restore.add_argument("--confirm", action="store_true", help="Confirm the previewed restore")
    backup_commands.add_parser("recover", help="Roll back an interrupted restore switch")
    return parser


def _run_paths() -> int:
    """Print path diagnostics without creating state, opening the database, or importing API."""

    paths = get_runtime_paths()
    for label, path in (
        ("data", paths.data_dir),
        ("config", paths.config_dir),
        ("logs", paths.log_dir),
        ("cache", paths.cache_dir),
        ("temp", paths.temp_dir),
        ("resources", paths.resources_dir),
    ):
        print(f"{label}: {path}")
    return 0


def _run_lifecycle(args: argparse.Namespace) -> int:
    """Run package-shell actions through the authenticated lifecycle adapter."""

    from scanner.runtime.native_lifecycle import NativeInstance, NativeLifecycleAdapter

    paths = get_runtime_paths()
    instance = NativeInstance(paths)
    if args.action == "status":
        status = instance.status()
        print(json.dumps({"running": status is not None, "instance": status}, sort_keys=True))
        return 0 if status is not None else 1
    if args.action == "open-ui":
        return 0 if instance.open_ui() else 1
    if args.action == "open-logs":
        # A stopped instance has no control listener. Opening its documented log
        # directory still goes through the same package adapter rather than
        # teaching every native shell its own platform-specific path behavior.
        if instance.open_logs():
            return 0
        return 0 if NativeLifecycleAdapter().open_logs(paths.log_dir) else 1
    if args.action == "quit":
        return 0 if instance.quit() else 1
    raise AssertionError("Unrecognized lifecycle action")  # pragma: no cover


def resolve_server_launch_context(
    args: argparse.Namespace,
    *,
    configuration=settings,
    environment: dict[str, str] | None = None,
    original_argv: tuple[str, ...] | None = None,
) -> ServerLaunchContext:
    """Resolve a service-generation snapshot before importing or binding HTTP.

    This function is intentionally independent of Uvicorn.  ``admin``, ``migration``,
    and ``backup`` commands never invoke it, so an unreachable browser configuration
    or missing frontend artifact cannot block them.
    """

    from scanner.backup.activation import GENERATION, JOURNAL, recover_restore
    from scanner.persistence.migrations.preflight import database_location

    database_path, data_dir = database_location()
    if (data_dir / JOURNAL).exists() or (data_dir / GENERATION).exists():
        recover_restore(database_path, data_dir)
    snapshot = resolve_deployment_snapshot(
        store=DeploymentStore(Path(configuration.data_dir)),
        cli_port_override=args.port,
        environment=environment,
        bind_host=args.host,
        auth_cookie_secure=configuration.auth_cookie_secure,
        auth_cookie_secure_overridden=configuration.is_environment_override("auth_cookie_secure"),
    )
    return ServerLaunchContext(
        host=args.host,
        cli_port_override=args.port,
        api_only=args.api_only,
        snapshot=snapshot,
        original_argv=original_argv if original_argv is not None else tuple(sys.argv[1:]),
    )


def _run_server(args: argparse.Namespace) -> None:
    """Resolve the deployment settings and start the HTTP controller or child."""
    from scanner.runtime.restart_service import run_controller

    try:
        # A frozen application is the normal end-user entry point.  Source mode
        # retains its foreground diagnostics unless an isolated test/package shell
        # explicitly opts into the same lifecycle adapter.
        native_launcher = not args.foreground and (
            is_frozen_application() or os.environ.get("EDITARR_NATIVE_LAUNCHER") == "1"
        )
        configure_runtime_logging(
            settings.log_dir,
            process_role="controller" if native_launcher else None,
            debug_enabled=debug_logging_enabled(settings.data_dir),
        )
        if native_launcher:
            # Do this before configuration resolution: a Windows GUI executable
            # may have neither standard stream, including on a bind/config error.
            from scanner.runtime.runtime_logging import redirect_standard_streams

            redirect_standard_streams(settings.log_dir, "controller")
        if not native_launcher:
            context = resolve_server_launch_context(args)
            if args.foreground and is_frozen_application():
                # Private frozen children inherit this diagnostic marker, but do
                # not leak it into a caller that invokes the CLI in-process.
                previous = os.environ.get("EDITARR_NATIVE_FOREGROUND")
                os.environ["EDITARR_NATIVE_FOREGROUND"] = "1"
                try:
                    run_controller(context, on_ready=_print_ready_url)
                finally:
                    if previous is None:
                        os.environ.pop("EDITARR_NATIVE_FOREGROUND", None)
                    else:
                        os.environ["EDITARR_NATIVE_FOREGROUND"] = previous
            else:
                run_controller(context)
            return

        from scanner.runtime.native_lifecycle import NativeInstance

        open_browser = not args.no_browser and _desktop_browser_available()
        instance = NativeInstance(get_runtime_paths())
        if instance.activate_existing(open_ui=open_browser):
            return
        context = resolve_server_launch_context(args)
        if not instance.claim_or_open_existing(
            context.snapshot.base_url + "/", open_ui=open_browser
        ):
            return

        def ready(snapshot: DeploymentSnapshot) -> None:
            instance.ready(snapshot.base_url + "/", open_ui=open_browser)
            if not open_browser:
                _print_ready_url(snapshot)

        try:
            run_controller(
                context,
                stop_event=instance.quit_event,
                on_ready=ready,
            )
        finally:
            instance.close()
    except (
        DeploymentConfigurationError,
        DeploymentConfigurationUnavailable,
        RuntimeError,
    ) as error:
        raise _CommandError(
            "Unable to run HTTP service: "
            f"{error}. Correct deployment settings or use --port for temporary recovery."
        ) from None


def main() -> None:
    # Argparse would echo an unrecognized value after ``--password``. Intercept it so
    # an accidentally supplied secret cannot be copied to terminal output.
    """Parse command-line arguments and dispatch the selected command."""
    if any(
        argument == "--password" or argument.startswith("--password=") for argument in sys.argv[1:]
    ):
        print(
            "error: --password is not supported; use a prompt or --password-stdin",
            file=sys.stderr,
        )
        raise SystemExit(2)
    args = _build_parser().parse_args()
    if args.command is None:
        try:
            _run_server(args)
        except _CommandError as error:
            print(f"error: {error}", file=sys.stderr)
            raise SystemExit(1) from None
        return
    try:
        if args.command == "paths":
            exit_code = _run_paths()
        elif args.command == "lifecycle":
            exit_code = _run_lifecycle(args)
        elif args.command == "migration":
            from scanner.commands.migration import run_migration_command

            try:
                print(
                    run_migration_command(args.migration_action, getattr(args, "destination", None))
                )
            except (RuntimeError, ValueError, OSError, sqlite3.Error, SQLAlchemyError) as error:
                raise _CommandError(f"Migration failed: {error}") from None
            exit_code = 0
        elif args.command == "backup":
            from scanner.commands.backup import run_backup_command

            exit_code = run_backup_command(args)
        else:
            exit_code = run_admin_command(args)
    except (_CommandError, AdminCommandError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
