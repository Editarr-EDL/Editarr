"""Owner status and committed-export writes under `editarr admin`.

There is no owner password and no lockout recovery. The README's "Jellyfin
accounts only" section says what an operator can do when the upgrade disabled an
owner without a Jellyfin link.

`write-export` runs beside the API. Like every database user it holds the
maintenance lease (`runtime.maintenance`), so it refuses during a backup, restore, or
other database maintenance, and it writes under the export publication lock
(`export_committed.write_committed`). Jellyfin must accept the owner's password; a
refused password records an `export.write_committed` event like a write does. It
only ever creates a missing file.
"""

import argparse
import getpass
import sqlite3
import sys

from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from scanner.db import get_session_factory, init_db
from scanner.persistence.repository import export_committed
from scanner.runtime.maintenance import MaintenanceUnavailable
from scanner.security.access.fresh import PasswordCheck
from scanner.security.account_repository import is_linked
from scanner.security.auth_repository import owner_id
from scanner.security.jellyfin_accounts.reauthentication import check_linked_password


class AdminCommandError(RuntimeError):
    """A concise, secret-free CLI failure suitable for an operator."""


def _stdin_password() -> SecretStr:
    if sys.stdin.isatty():
        raise AdminCommandError("--password-stdin requires non-interactive standard input")
    password = sys.stdin.read()
    if password.endswith("\n"):
        password = password[:-1]
        if password.endswith("\r"):
            password = password[:-1]
    return SecretStr(password)


def _prepare_database() -> None:
    """Reject incompatible or unresolved schemas before admin commands query them."""
    try:
        from scanner.backup.activation import GENERATION, JOURNAL, recover_restore
        from scanner.persistence.migrations.preflight import database_location

        database_path, data_dir = database_location()
        if (data_dir / JOURNAL).exists() or (data_dir / GENERATION).exists():
            recover_restore(database_path, data_dir)
        init_db()
    except (RuntimeError, ValueError, OSError, sqlite3.Error, SQLAlchemyError) as error:
        raise AdminCommandError(f"Database preflight failed: {error}") from None


def run_admin_command(args: argparse.Namespace) -> int:
    """Execute an admin command against the configured data directory."""
    if args.admin_command == "status":
        return _status()
    try:
        return _write_export(args)
    except MaintenanceUnavailable:
        raise AdminCommandError(
            "Database maintenance holds this data directory; retry when it finishes"
        ) from None


def _status() -> int:
    _prepare_database()
    with get_session_factory()() as db:
        configured = owner_id(db) is not None
    print(f"Administrator status: {'configured' if configured else 'setup required'}.")
    return 0


def _write_export(args: argparse.Namespace) -> int:
    """Write one publication's committed bytes to its recorded path as the owner.

    Jellyfin checks the owner's password against the owner's linked user. An empty
    password is refused and audited like any other wrong one.
    """
    _prepare_database()
    try:
        with get_session_factory()() as db:
            owner = owner_id(db)
            if owner is None:
                raise AdminCommandError("No owner is configured")
            if not is_linked(db, owner):
                raise AdminCommandError(
                    "The owner has no Jellyfin link, so Jellyfin cannot confirm the owner. "
                    "Reinstall, or recover the pre-upgrade snapshot with `editarr migration "
                    "recover-export` and link the owner with the previous release (see the "
                    "README)"
                )
            if args.password_stdin:
                password = _stdin_password()
            else:
                password = SecretStr(getpass.getpass("Owner's Jellyfin password: "))
            check = check_linked_password(db, owner, password.get_secret_value())
            if check is PasswordCheck.UNAVAILABLE:
                raise AdminCommandError(
                    "Jellyfin could not be reached to confirm the owner; nothing was written "
                    "or recorded. Retry when Jellyfin answers"
                )
            if check is PasswordCheck.UNNAMED:
                export_committed.refuse(db, args.export_id, owner, "jellyfin_name_unknown")
                raise AdminCommandError(
                    "Editarr has not stored the owner's Jellyfin name yet, so it cannot ask "
                    "Jellyfin to check the password; start Editarr so it syncs the owner "
                    "with Jellyfin, then retry"
                )
            if check is PasswordCheck.REFUSED:
                export_committed.refuse(db, args.export_id, owner, "reauthentication_failed")
                raise AdminCommandError("Jellyfin did not accept the owner's password")
            path, created = export_committed.write_committed(db, args.export_id, owner)
    except export_committed.WriteRefused as refusal:
        raise AdminCommandError(
            f"Export {args.export_id} was not written ({refusal.code}): {refusal}"
        ) from None
    except export_committed.WriteUnrecorded as failure:
        outcome = (
            "the file it created was removed"
            if failure.removed
            else f"the file it created changed and was left at {failure.path}"
        )
        raise AdminCommandError(
            f"Export {args.export_id}: the database failed to record the write; {outcome}"
        ) from None
    except (SQLAlchemyError, sqlite3.Error):
        raise AdminCommandError(
            f"Export {args.export_id} was not written: the database refused the command"
        ) from None
    if created:
        print(f"Wrote export {args.export_id}'s committed bytes to {path}")
    else:
        print(f"Export {args.export_id}'s committed bytes are already present at {path}")
    return 0
