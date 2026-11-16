"""Headless portable backup operations using the shared offline services."""

import getpass
import json
import sys
from pathlib import Path

from scanner.backup.activation import GENERATION, JOURNAL, activate_restore, recover_restore
from scanner.backup.archive import BackupError, export_backup
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused
from scanner.persistence.migrations.preflight import database_location


def _passphrase(args, *, export: bool = False) -> str:
    if args.password_stdin:
        if sys.stdin.isatty():
            raise BackupError("--password-stdin requires non-interactive standard input")
        value = sys.stdin.readline().rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise BackupError("Interactive passphrase requires a terminal; use --password-stdin")
        try:
            value = getpass.getpass("Backup passphrase: ")
            if export and value != getpass.getpass("Confirm backup passphrase: "):
                raise BackupError("Passphrases do not match")
        except (EOFError, KeyboardInterrupt):
            raise BackupError(
                "Passphrase prompt cancelled; use --password-stdin headlessly"
            ) from None
    if not value:
        raise BackupError("Backup passphrase must not be empty")
    return value


def _archive_passphrase(args) -> str | None:
    with Path(args.archive).open("rb") as stream:
        encrypted = stream.read(22) == b"age-encryption.org/v1\n"
    if args.password_stdin and not encrypted:
        raise BackupError("Plain backups do not accept a passphrase")
    return _passphrase(args) if encrypted else None


def run_backup_command(args) -> int:
    """Run an offline command, returning 2 for cancelled confirmation and 1 for failures."""
    try:
        database, data_dir = database_location()
        if args.backup_action == "recover":
            recovered = recover_restore(database, data_dir)
            print(
                "Interrupted restore rolled back." if recovered else "No interrupted restore found."
            )
            return 0
        if args.backup_action == "export":
            if (data_dir / JOURNAL).exists() or (data_dir / GENERATION).exists():
                raise BackupError("Restore activation needs offline recovery before export")
            if args.password_stdin and not args.encrypt:
                raise BackupError("--password-stdin requires --encrypt")
            passphrase = _passphrase(args, export=True) if args.encrypt else None
            print("Exporting backup...", file=sys.stderr, flush=True)
            export_backup(database, data_dir, Path(args.destination), passphrase=passphrase)
            print("Backup exported.")
            return 0
        passphrase = _archive_passphrase(args)
        print("Validating backup...", file=sys.stderr, flush=True)
        with preview_restore(
            Path(args.archive), data_dir, target_database=database, passphrase=passphrase
        ) as preview:
            print(json.dumps(preview.plan, indent=2, sort_keys=True))
            if args.backup_action == "inspect":
                return 0
            if not args.confirm:
                try:
                    accepted = (
                        sys.stdin.isatty() and input("Type RESTORE to activate: ") == "RESTORE"
                    )
                except (EOFError, KeyboardInterrupt):
                    accepted = False
                if not accepted:
                    print("Restore cancelled.", file=sys.stderr)
                    return 2
            print("Activating restore...", file=sys.stderr, flush=True)
            report = activate_restore(preview, database, data_dir, confirmed=True)
        binding = report["backup_deployment"]
        print(
            "Restore activated; the queue is paused pending reconciliation. Review media "
            f"paths and source connections. {len(report['move_disagreements'])} transfer or "
            "link move records disagree with the disk; see restore-report.json.\n"
            "This installation keeps its own base URL and port. The backup used base URL "
            f"{binding['base_url'] or 'default'} and port {binding['port'] or 'default'}. "
            "To adopt them, change them under Deployment in Settings."
        )
        return 0
    except RestoreRefused as refusal:
        print(f"error: Restore refused ({refusal.reason}): {refusal}", file=sys.stderr)
        return 1
    except (BackupError, RuntimeError, ValueError, OSError) as error:
        print(f"error: Backup operation failed: {error}", file=sys.stderr)
        return 1
