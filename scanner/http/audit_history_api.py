"""Audit history is separate from diagnostic log downloads and settings mutations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access
from scanner.security.audit.history import AuditHistoryBusy, read_history
from scanner.security.audit.query import AuditEntry, AuditFilters, AuditPage
from scanner.security.auth_policy import session_now

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Vary"] = "Cookie, Authorization"


def _read(db, access, filters, *, event_id=None):
    try:
        return read_history(db, access, filters, now=session_now(), event_id=event_id)
    except AuditHistoryBusy:
        response = JSONResponse(
            status_code=503,
            content={"code": "audit_history_busy", "detail": "Audit history is busy. Try again."},
            headers={"Retry-After": "1"},
        )
        _private(response)
        return response


@router.get("", response_model=AuditPage)
def audit_list(
    response: Response,
    filters: Annotated[AuditFilters, Query()],
    access: PrincipalAccess = Depends(request_access),
    db: Session = Depends(get_db),
):
    _private(response)
    return _read(db, access, filters)


@router.get("/{event_id}", response_model=AuditEntry)
def audit_detail(
    event_id: UUID,
    response: Response,
    access: PrincipalAccess = Depends(request_access),
    db: Session = Depends(get_db),
):
    _private(response)
    return _read(db, access, AuditFilters(), event_id=str(event_id))
