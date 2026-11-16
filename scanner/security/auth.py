"""Signing a browser session out."""

from datetime import datetime

from sqlalchemy.orm import Session

from scanner.security.access.vocabulary import Role
from scanner.security.auth_audit import AuditContext, AuthenticationAuditLogger, new_request_id
from scanner.security.auth_repository import AuthenticatedSession, revoke_session_by_id


def logout(
    db: Session,
    session: AuthenticatedSession,
    *,
    audit: AuthenticationAuditLogger | None,
    context: AuditContext | None = None,
    now: datetime | None = None,
) -> bool:
    """Revoke one browser session; other sessions, including the same user's, stay valid."""

    revoked = revoke_session_by_id(db, session.id, now=now)
    if audit is not None:
        audit.emit(
            event="auth.logout" if revoked else "auth.session_rejected",
            outcome="success" if revoked else "denied",
            severity="info" if revoked else "warning",
            context=context or AuditContext(source="system", request_id=new_request_id()),
            actor=("administrator" if session.role is Role.OWNER else "user")
            if revoked
            else "anonymous",
            admin_user_id=session.user_id if revoked else None,
            session_id=session.id if revoked else None,
            now=now,
        )
    return revoked
