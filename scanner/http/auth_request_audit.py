"""Authentication-log context and throttle auditing for the HTTP sign-in endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import Request
from sqlalchemy.orm import Session

from scanner.security.access.records import AuditOutcome
from scanner.security.audit import authentication as stored_audit
from scanner.security.auth_audit import AuditContext, AuthenticationAuditLogger, new_request_id

from .auth_rate_limit import LoginRateLimiter


def authentication_log(request: Request) -> AuthenticationAuditLogger | None:
    """The application's authentication log; a bare test router has none."""

    return getattr(request.app.state, "authentication_audit", None)


def audit_context(request: Request, client_address: str | None) -> AuditContext:
    """Sanitized metadata for this request's one authentication-log record."""

    return AuditContext(
        source="http",
        request_id=new_request_id(),
        client_address=client_address,
        http_method=request.method,
        route=request.scope["route"].path,
    )


def record_throttled(
    db: Session,
    request: Request,
    limiter: LoginRateLimiter,
    *,
    username: str,
    address: str,
    client_address: str | None,
    now: datetime,
    method: stored_audit.SignInMethod | None = None,
) -> None:
    """Log every refused attempt, and store only the first refusal of each lockout.

    Storing each 429 would let unauthenticated requests grow the audit table
    without bound; a lockout needs real failed attempts first.
    """

    log = authentication_log(request)
    if log is not None:
        log.emit(
            event="auth.login_throttled",
            outcome="denied",
            severity="warning",
            context=audit_context(request, client_address),
            actor="anonymous",
            now=now,
        )
    if limiter.first_refusal(username=username, address=address):
        stored_audit.sign_in_refused(
            db, AuditOutcome.DENIED, "throttled", target=None, now=now, method=method
        )
