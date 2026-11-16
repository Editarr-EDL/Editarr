"""Secret-free JSONL audit records for authentication events."""

import ipaddress
import json
import logging
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

AuditEventName = Literal[
    "auth.audit_started",
    "auth.setup",
    "auth.login",
    "auth.login_throttled",
    "auth.logout",
    "auth.account_changed",
    "auth.security_changed",
    "auth.sessions_revoked",
    "auth.session_rejected",
    "auth.csrf_rejected",
    "auth.origin_rejected",
]
AuditOutcome = Literal["success", "failure", "denied"]
AuditSeverity = Literal["info", "warning", "error"]
AuditSource = Literal["http", "cli", "system"]
AuditActor = Literal["administrator", "user", "anonymous", "system"]

_AUDIT_LOGGER_NAME = "scanner.security.auth.audit"
_EVENT_NAMES = frozenset(AuditEventName.__args__)
_OUTCOMES = frozenset(AuditOutcome.__args__)
_SEVERITIES = frozenset(AuditSeverity.__args__)
_SOURCES = frozenset(AuditSource.__args__)
_ACTORS = frozenset(AuditActor.__args__)
_REASON_CODES = frozenset(
    {"invalid_credentials", "account_disabled", "jellyfin_unavailable", "jellyfin_user_blocked"}
)
_CHANGED_FIELDS = frozenset({"authentication_required", "method", "password", "username"})
_ROUTES = frozenset(
    {
        "/api/auth/status",
        "/api/auth/setup",
        "/api/auth/jellyfin/login",
        "/api/auth/jellyfin/quick-connect/initiate",
        "/api/auth/jellyfin/quick-connect/complete",
        "/api/auth/logout",
    }
)
# The response status each authentication outcome produces, for records written
# before the handler returns.
_HTTP_STATUS = {
    ("auth.setup", "success"): 201,
    ("auth.setup", "failure"): 409,
    ("auth.login", "success"): 200,
    ("auth.login", "failure"): 401,
    ("auth.login", "denied"): 403,
    ("auth.login_throttled", "denied"): 429,
    ("auth.logout", "success"): 200,
    ("auth.session_rejected", "denied"): 200,
    ("auth.account_changed", "success"): 200,
    ("auth.sessions_revoked", "success"): 200,
}
_ROTATED_NAME_PREFIX = "auth-audit-"
_ROTATED_NAME_SUFFIX = ".jsonl"
_RETENTION_DAYS = 30
_FAILURE_LOG_INTERVAL = timedelta(minutes=1)

_application_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Typed, already-sanitized request metadata for an audit record.

    An HTTP context may omit `client_address` when no canonical peer address
    exists, and `http_status` when the record is written before the response;
    emission then fills the status from the event and outcome.
    """

    source: AuditSource
    request_id: str | None = None
    client_address: str | None = None
    http_method: str | None = None
    route: str | None = None
    http_status: int | None = None

    def __post_init__(self) -> None:
        """Validate and normalize audit context fields before they are recorded."""
        if self.source == "http":
            if self.request_id is None:
                raise ValueError("HTTP audit context requires a server request ID")
            if self.http_method is None or self.route is None:
                raise ValueError("HTTP audit context requires method and route")
        elif any(
            value is not None
            for value in (
                self.client_address,
                self.http_method,
                self.route,
                self.http_status,
            )
        ):
            raise ValueError("Only HTTP audit contexts may contain HTTP metadata")
        if self.request_id is not None:
            try:
                if uuid.UUID(self.request_id).version != 4:
                    raise ValueError
            except (AttributeError, ValueError) as error:
                raise ValueError("Authentication audit request ID must be a UUIDv4") from error
        if self.source == "http":
            if self.client_address is not None:
                try:
                    ipaddress.ip_address(self.client_address)
                except ValueError as error:
                    raise ValueError(
                        "Authentication audit client address must be canonical IP"
                    ) from error
            if self.http_method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                raise ValueError("Authentication audit HTTP method is not allowlisted")
            if self.route not in _ROUTES:
                raise ValueError("Authentication audit route is not allowlisted")
            if self.http_status is not None and not 100 <= self.http_status <= 599:
                raise ValueError("Authentication audit HTTP status is invalid")


class AuthenticationAuditLogger:
    """Append authenticated-operation records to the protected audit JSONL stream."""

    def __init__(self, log_dir: Path, *, now: datetime | None = None) -> None:
        """Write under the resolved diagnostic-state root, never under data/cache."""

        self._directory = Path(log_dir)
        self._path = self._directory / "auth-audit.jsonl"
        self._logger = logging.getLogger(_AUDIT_LOGGER_NAME)
        self._logger.propagate = False
        self._logger.handlers.clear()
        self._logger.addHandler(_AuthenticationAuditHandler(self))
        self._logger.setLevel(logging.INFO)
        self._last_failure_at: datetime | None = None
        self._current_day: date | None = None
        try:
            current_time = _utc_now(now)
            self._initialize(current_time)
            self.emit(
                event="auth.audit_started",
                outcome="success",
                severity="info",
                context=AuditContext(source="system"),
                actor="system",
                now=current_time,
            )
        except OSError:
            self._report_failure(_utc_now(now))

    def emit(
        self,
        *,
        event: AuditEventName,
        outcome: AuditOutcome,
        severity: AuditSeverity,
        context: AuditContext,
        actor: AuditActor,
        admin_user_id: int | None = None,
        session_id: int | None = None,
        reason_code: str | None = None,
        changed_fields: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> None:
        """Write one allowlisted, stable-schema audit record without user-supplied values."""

        _validate_record_values(
            event=event,
            outcome=outcome,
            severity=severity,
            context=context,
            actor=actor,
            admin_user_id=admin_user_id,
            session_id=session_id,
            reason_code=reason_code,
            changed_fields=changed_fields,
        )
        current_time = _utc_now(now)
        http_status = context.http_status
        if context.source == "http" and http_status is None:
            http_status = _HTTP_STATUS[(event, outcome)]
        record = {
            "schema_version": 1,
            "occurred_at": _rfc3339_milliseconds(current_time),
            "event_id": str(uuid.uuid4()),
            "request_id": context.request_id,
            "application": "editarr",
            "event": event,
            "outcome": outcome,
            "severity": severity,
            "source": context.source,
            "client_address": context.client_address,
            "actor": actor,
            "admin_user_id": admin_user_id,
            "session_id": session_id,
            "http_method": context.http_method,
            "route": context.route,
            "http_status": http_status,
            "reason_code": reason_code,
            "changed_fields": list(changed_fields),
        }
        try:
            self._rotate_if_needed(current_time)
            encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            self._logger.info(encoded)
        except (OSError, TypeError, ValueError):
            self._report_failure(current_time)

    def _initialize(self, current_time: datetime) -> None:
        self._ensure_directory()
        self._current_day = current_time.date()
        self._cleanup_retention(current_time.date())

    def _rotate_if_needed(self, current_time: datetime) -> None:
        """Rotate the audit file when the UTC date changes."""
        if self._current_day is None:
            self._initialize(current_time)
        if self._current_day == current_time.date():
            return
        if self._path.exists():
            rotated = self._directory / _rotated_file_name(self._current_day)
            if rotated.exists():
                raise OSError("Refusing to overwrite an existing authentication audit segment")
            os.replace(self._path, rotated)
            _restrict_file_mode(rotated)
        self._current_day = current_time.date()
        self._cleanup_retention(current_time.date())

    def _ensure_directory(self) -> None:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _restrict_directory_mode(self._directory)

    def _append(self, encoded: str) -> None:
        """Append one serialized audit record while preserving private file permissions."""
        self._ensure_directory()
        descriptor = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            _restrict_file_mode(self._path)
            with os.fdopen(descriptor, "a", encoding="utf-8", closefd=True) as stream:
                descriptor = -1
                stream.write(encoded)
                stream.write("\n")
                stream.flush()
        finally:
            if descriptor != -1:
                os.close(descriptor)

    def _cleanup_retention(self, current_day: date) -> None:
        for candidate in self._directory.iterdir():
            rotated_day = _rotated_file_date(candidate.name)
            if rotated_day is not None and (current_day - rotated_day).days > _RETENTION_DAYS:
                candidate.unlink()

    def _report_failure(self, current_time: datetime) -> None:
        if (
            self._last_failure_at is not None
            and current_time - self._last_failure_at < _FAILURE_LOG_INTERVAL
        ):
            return
        self._last_failure_at = current_time
        _application_logger.error(
            "Authentication audit log is unavailable; authentication continues"
        )


class _AuthenticationAuditHandler(logging.Handler):
    """A handler that makes the dedicated logger's sole sink the protected JSONL file."""

    def __init__(self, writer: AuthenticationAuditLogger) -> None:
        super().__init__()
        self._writer = writer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._writer._append(record.getMessage())
        except OSError:
            self._writer._report_failure(datetime.now(timezone.utc))


def new_request_id() -> str:
    """Create a server-side UUIDv4 operation identifier for a future audit context."""

    return str(uuid.uuid4())


def _validate_record_values(
    *,
    event: str,
    outcome: str,
    severity: str,
    context: AuditContext,
    actor: str,
    admin_user_id: int | None,
    session_id: int | None,
    reason_code: str | None,
    changed_fields: tuple[str, ...],
) -> None:
    """Reject audit fields that could inject control characters or sensitive content."""
    if event not in _EVENT_NAMES:
        raise ValueError("Authentication audit event is not allowlisted")
    if outcome not in _OUTCOMES or severity not in _SEVERITIES:
        raise ValueError("Authentication audit outcome or severity is not allowlisted")
    if context.source not in _SOURCES or actor not in _ACTORS:
        raise ValueError("Authentication audit source or actor is not allowlisted")
    if admin_user_id is not None and (type(admin_user_id) is not int or admin_user_id < 1):
        raise ValueError("Authentication audit user ID must be a positive integer or null")
    if session_id is not None and (not isinstance(session_id, int) or session_id < 1):
        raise ValueError("Authentication audit session ID must be a positive integer or null")
    if reason_code is not None and reason_code not in _REASON_CODES:
        raise ValueError("Authentication audit reason is not allowlisted")
    if event == "auth.audit_started":
        if context.source != "system" or context.request_id is not None:
            raise ValueError(
                "Authentication audit startup must use the system null request context"
            )
    elif context.request_id is None:
        raise ValueError("Authentication audit event requires a server operation request ID")
    if tuple(sorted(changed_fields)) != changed_fields or len(set(changed_fields)) != len(
        changed_fields
    ):
        raise ValueError("Authentication audit changed fields must be unique and sorted")
    if not set(changed_fields).issubset(_CHANGED_FIELDS):
        raise ValueError("Authentication audit changed field is not allowlisted")


def _restrict_directory_mode(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        path.chmod(mode & ~0o077)


def _restrict_file_mode(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        path.chmod(mode & ~0o077)


def _rotated_file_name(rotation_day: date) -> str:
    return f"{_ROTATED_NAME_PREFIX}{rotation_day.isoformat()}{_ROTATED_NAME_SUFFIX}"


def _rotated_file_date(name: str) -> date | None:
    if not name.startswith(_ROTATED_NAME_PREFIX) or not name.endswith(_ROTATED_NAME_SUFFIX):
        return None
    day_value = name.removeprefix(_ROTATED_NAME_PREFIX).removesuffix(_ROTATED_NAME_SUFFIX)
    try:
        return date.fromisoformat(day_value)
    except ValueError:
        return None


def _utc_now(value: datetime | None) -> datetime:
    current_time = value or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise ValueError("Authentication audit timestamps must be timezone-aware")
    return current_time.astimezone(timezone.utc)


def _rfc3339_milliseconds(value: datetime) -> str:
    return _utc_now(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")
