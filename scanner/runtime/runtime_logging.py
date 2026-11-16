"""Private rotating log sinks shared by controller and HTTP processes."""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from scanner.runtime.runtime_paths import ensure_user_private_directory

_LOG_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5
# ``application`` remains the source/default root sink. Native process roles get
# their own files so a controller failure, HTTP-generation failure, or scan-worker
# failure can be diagnosed without interleaved output from another process.
_ROLE_NAMES = ("application", "controller", "http", "worker")
_SENSITIVE_VALUE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:[A-Za-z0-9_-]*(?:password|token|secret|csrf|session|"
    r"authorization|cookie|api[_-]?key|authkey|capability)[A-Za-z0-9_-]*)[\"']?\s*(?:=|:)\s*)"
    r"(\"(?:[^\"\\\r\n]|\\.)*\"?|'(?:[^'\\\r\n]|\\.)*'?|[^\r\n,;]+)|"
    r"(\b(?:password|token|secret|csrf(?:[_-]?key)?|session(?:[_-]?(?:id|token|key))?)"
    r"\b\s+)([^\s,;]+)"
)


def redact_diagnostic(value: object) -> str:
    """Return diagnostic text with common credential-shaped values removed.

    Launcher-controlled standard streams and logging are intentionally a last-resort
    diagnostic sink.  Application code should still avoid putting secrets in errors,
    but this filter prevents a malformed startup command from making a token or
    session value durable in a native log.
    """

    def replace(match: re.Match[str]) -> str:
        return f"{match.group(1) or match.group(3)}[REDACTED]"

    return _SENSITIVE_VALUE.sub(replace, str(value))


def configure_runtime_logging(
    log_dir: Path, *, process_role: str | None = None, debug_enabled: bool = False
) -> dict[str, Path]:
    """Configure owner-private rotating sinks at ``log_dir``.

    The returned paths are diagnostic-only and contain no credential material.
    Calling this repeatedly is safe: a process installs each marked handler once.
    """

    directory = ensure_user_private_directory(Path(log_dir))
    set_scanner_log_level(debug_enabled)
    paths = {role: directory / f"{role}.log" for role in _ROLE_NAMES}
    if process_role is not None:
        if process_role not in _ROLE_NAMES:
            raise ValueError(f"unknown native log role: {process_role}")
        _configure_logger(logging.getLogger(), paths[process_role], process_role)
        return paths
    _configure_logger(logging.getLogger(), paths["application"], "application")
    _configure_logger(
        logging.getLogger("scanner.runtime.restart_service"), paths["controller"], "controller"
    )
    _configure_logger(logging.getLogger("scanner.api"), paths["http"], "http")
    _configure_logger(logging.getLogger("scanner.worker"), paths["worker"], "worker")
    return paths


def set_scanner_log_level(debug_enabled: bool) -> None:
    """Set Editarr's own verbosity without enabling noisy dependency debug logs."""

    logging.getLogger("scanner").setLevel(logging.DEBUG if debug_enabled else logging.INFO)


def _configure_logger(logger: logging.Logger, path: Path, role: str) -> None:
    """Attach a private rotating file handler to the selected log channel."""
    marker = f"editarr-runtime-log:{role}:{path}"
    if any(
        getattr(handler, "_editarr_runtime_marker", None) == marker for handler in logger.handlers
    ):
        return
    handler = _PrivateRotatingFileHandler(
        path,
        maxBytes=_LOG_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler._editarr_runtime_marker = marker  # type: ignore[attr-defined]
    handler.setFormatter(
        _RedactingFormatter("%(asctime)s %(process)d %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(_RedactingFilter())
    logger.addHandler(handler)


class _PrivateRotatingFileHandler(RotatingFileHandler):
    """Tighten file permissions after every open/rollover on POSIX filesystems."""

    def _open(self):  # type: ignore[override]
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


class _RedactingFilter(logging.Filter):
    """Redact a record before the native rotating-file handler formats it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_diagnostic(record.getMessage())
        record.args = ()
        return True


class _RedactingFormatter(logging.Formatter):
    """Redact formatted exception text as well as ordinary record messages."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_diagnostic(super().format(record))


class _RedactingStream:
    """A text stream backed by an existing rotating logging handler."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, handler: logging.Handler, level: int, stream_name: str):
        self._handler = handler
        self._level = level
        self._stream_name = stream_name
        self._pending = ""

    def write(self, value: str) -> int:
        self._pending += str(value)
        lines = self._pending.splitlines(keepends=True)
        self._pending = ""
        for line in lines:
            if line.endswith(("\n", "\r")):
                text = line.rstrip("\r\n")
                if text:
                    self._emit(text)
            else:
                self._pending = line
        return len(value)

    def flush(self) -> None:
        if self._pending:
            self._emit(self._pending)
            self._pending = ""
        self._handler.flush()

    def isatty(self) -> bool:
        return False

    def _emit(self, value: str) -> None:
        record = logging.LogRecord(self._stream_name, self._level, "", 0, "%s", (value,), None)
        self._handler.handle(record)


def redirect_standard_streams(log_dir: Path, role: str) -> None:
    """Direct native-process stdout/stderr to one private, redacting log file.

    Windows GUI executables commonly begin with both streams set to ``None``.
    Replacing both values rather than inspecting them lets normal startup and
    uncaught exceptions remain diagnosable in that environment.  Foreground source
    diagnostics deliberately do not call this function.
    """

    if role not in _ROLE_NAMES:
        raise ValueError(f"unknown native log role: {role}")
    directory = ensure_user_private_directory(Path(log_dir))
    path = directory / f"{role}.log"
    marker = f"editarr-runtime-log:{role}:{path}"
    handler = next(
        (
            candidate
            for candidate in logging.getLogger().handlers
            if getattr(candidate, "_editarr_runtime_marker", None) == marker
        ),
        None,
    )
    if handler is None:
        logger = logging.getLogger(f"editarr.native.stdio.{role}")
        logger.propagate = False
        _configure_logger(logger, path, role)
        handler = next(
            candidate
            for candidate in logger.handlers
            if getattr(candidate, "_editarr_runtime_marker", None) == marker
        )
    sys.stdout = _RedactingStream(handler, logging.INFO, f"{role}.stdout")  # type: ignore[assignment]
    sys.stderr = _RedactingStream(handler, logging.ERROR, f"{role}.stderr")  # type: ignore[assignment]
