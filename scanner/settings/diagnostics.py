"""User-visible, redacted diagnostics backed by the private runtime log directory."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scanner.runtime.maintenance import DatabaseMaintenance
from scanner.runtime.runtime_logging import redact_diagnostic
from scanner.runtime.runtime_paths import ensure_user_private_directory

LOG_CHANNELS = ("application", "controller", "http", "worker")
_PREFERENCES_FILENAME = "diagnostics.json"
_TAIL_BYTES = 256 * 1024
_MAX_LINES = 1_000


class DiagnosticsError(RuntimeError):
    """A safe diagnostics error suitable for the authenticated API."""


@dataclass(frozen=True, slots=True)
class DiagnosticsPreferences:
    debug_enabled: bool = False


def load_diagnostics_preferences(data_dir: Path) -> DiagnosticsPreferences:
    """Return durable diagnostics preferences, defaulting safely on first run."""

    path = Path(data_dir) / _PREFERENCES_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DiagnosticsPreferences()
    except OSError as exc:
        raise DiagnosticsError("Unable to read diagnostics settings") from exc
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise DiagnosticsError("Diagnostics settings are invalid") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("debug_enabled", False), bool):
        raise DiagnosticsError("Diagnostics settings are invalid")
    return DiagnosticsPreferences(debug_enabled=parsed.get("debug_enabled", False))


def debug_logging_enabled(data_dir: Path) -> bool:
    """Read the user preference without making application startup fragile."""

    try:
        return load_diagnostics_preferences(data_dir).debug_enabled
    except DiagnosticsError:
        logging.getLogger(__name__).warning(
            "Diagnostics settings are unavailable; using info logging"
        )
        return False


def save_diagnostics_preferences(data_dir: Path, preferences: DiagnosticsPreferences) -> None:
    """Atomically persist the non-secret diagnostics preference with private mode bits."""
    with DatabaseMaintenance(data_dir).worker_lease():
        _write_diagnostics_preferences(data_dir, preferences)


def _write_diagnostics_preferences(data_dir: Path, preferences: DiagnosticsPreferences) -> None:

    directory = ensure_user_private_directory(Path(data_dir))
    destination = directory / _PREFERENCES_FILENAME
    encoded = json.dumps({"debug_enabled": preferences.debug_enabled}, separators=(",", ":"))
    temporary_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".diagnostics-", suffix=".tmp", dir=directory)
        temporary_path = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, destination)
        temporary_path = None
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            try:
                os.fsync(directory_fd)
            except OSError:
                # The replacement succeeded. Some supported filesystems do not
                # allow syncing a directory, so do not report a false save failure.
                pass
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise DiagnosticsError("Unable to save diagnostics settings") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def read_log_tail(log_dir: Path, channel: str, *, limit: int) -> tuple[list[str], bool]:
    """Read a bounded, redacted tail from one allowlisted runtime log channel."""

    if channel not in LOG_CHANNELS:
        raise DiagnosticsError("Unknown log source")
    if limit < 1 or limit > _MAX_LINES:
        raise DiagnosticsError(f"Log line limit must be between 1 and {_MAX_LINES}")
    directory = Path(log_dir)
    path = directory / f"{channel}.log"
    try:
        if path.is_symlink() or not path.is_file():
            return [], False
        size = path.stat().st_size
        with path.open("rb") as handle:
            truncated = size > _TAIL_BYTES
            if truncated:
                handle.seek(-_TAIL_BYTES, os.SEEK_END)
                handle.readline()  # Drop a partial first record.
            data = handle.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return [], False
    except OSError as exc:
        raise DiagnosticsError("Unable to read log file") from exc
    lines = [redact_diagnostic(line) for line in data.splitlines()]
    return lines[-limit:], truncated or len(lines) > limit
