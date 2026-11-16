"""Each account's community connection: status, enrollment, profile, and signing keys.

`/api/community/...` acts on the caller's own identity. `/api/users/{user_id}/community/...`
names the account: an admin manages ordinary users' connections and the owner
anyone's, decided by own/other capability pairs. Every operation runs on the
named account's own identity home, so one account's enrollment secret, profile
intent, rotation candidate, or recovery journal never reaches another's.
Mutations are audited as `community.identity.change`. Responses are no-store and
never carry private keys, confirmation secrets, or candidate material.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.integrations.community.client import CommunityClient, absent_status
from scanner.integrations.community.recovery import CommunityRecovery
from scanner.integrations.community.rotation import CommunityRotation
from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.persistence.repository.repository_community import user_identity_home
from scanner.security import auth_policy
from scanner.security.access.authorization import HiddenAccount, require, resolve_account
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import DENIAL_DETAILS, AccessDenied
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import UserId
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.request_access import request_access
from scanner.security.access.targets import AccountRef, ActionPair
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import record_event
from scanner.security.private_identity_files import IdentityUnavailable
from scanner.settings.config import settings

router = APIRouter(prefix="/api/community", tags=["community"])
users_router = APIRouter(prefix="/api/users/{user_id}/community", tags=["community"])

_NO_STORE = {"Cache-Control": "no-store"}


def service_transport() -> CommunityTransport:
    """The configured community service, or `not_configured`."""
    if not settings.community_service_url or not settings.community_service_audience:
        raise CommunityFailure("not_configured")
    return CommunityTransport(
        ServiceEndpoint(settings.community_service_url, settings.community_service_audience)
    )


def local_client(db: Session, access: PrincipalAccess) -> CommunityClient:
    """A client on the caller's own identity, for community activities.

    An account that never started enrollment has no identity and is `not_connected`.
    """
    # `request_access` read the principal in this session; the workflows require a clean one.
    db.rollback()
    transport = service_transport()
    home = user_identity_home(db, settings.data_dir, access.principal.user_id)
    if home is None:
        raise CommunityFailure("not_connected")
    return CommunityClient(db, home, transport)


class EnrollmentConfirmation(BaseModel):
    state_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool


class ProfileUpdate(BaseModel):
    display_name: str = Field(min_length=3, max_length=40)
    operation_id: str = Field(pattern=r"^[A-Za-z0-9._~-]{1,128}$")


class RotationConfirmation(BaseModel):
    confirmed: bool


class RecoveryStart(RotationConfirmation):
    old_key_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class Change(StrEnum):
    """The `change` detail of an audited `community.identity.change`."""

    ENROLLMENT_START = "enrollment_start"
    ENROLLMENT_COMPLETE = "enrollment_complete"
    ENROLLMENT_CANCEL = "enrollment_cancel"
    DISCONNECT = "disconnect"
    PROFILE_RENAME = "profile_rename"
    PROFILE_RENAME_CANCEL = "profile_rename_cancel"
    KEY_ROTATION = "key_rotation"
    KEY_ROTATION_RETRY = "key_rotation_retry"
    KEY_RECOVERY_START = "key_recovery_start"
    KEY_RECOVERY_COMPLETE = "key_recovery_complete"
    KEY_RECOVERY_RETRY = "key_recovery_retry"


_VIEW = ActionPair(C.COMMUNITY_IDENTITY_VIEW_OWN, C.COMMUNITY_IDENTITY_VIEW_OTHER)
_ENROLL = ActionPair(C.COMMUNITY_IDENTITY_ENROLL_OWN, C.COMMUNITY_IDENTITY_ENROLL_OTHER)
_PROFILE = ActionPair(C.COMMUNITY_IDENTITY_PROFILE_OWN, C.COMMUNITY_IDENTITY_PROFILE_OTHER)
_KEYS = ActionPair(C.COMMUNITY_IDENTITY_KEYS_OWN, C.COMMUNITY_IDENTITY_KEYS_OTHER)
_DISCONNECT = ActionPair(C.COMMUNITY_IDENTITY_DISCONNECT_OWN, C.COMMUNITY_IDENTITY_DISCONNECT_OTHER)

_UNCONFIGURED_CONNECTION = {
    "configured": False,
    "connected": False,
    "profile": None,
    "pending": None,
}
_UNCONFIGURED_RECOVERY = {"configured": False, "identity": None, "pending": None, "completed": None}
_ABSENT_RECOVERY = {"configured": True, "identity": None, "pending": None, "completed": None}


@dataclass(frozen=True)
class Operation:
    """One identity route, registered for the caller and for a named account.

    `absent` answers a read for an account without an identity, and `unconfigured`
    answers it when no service is configured; a route without them refuses instead.
    Only `create` routes reserve an identity for an account that has none.
    """

    method: str
    path: str
    action: ActionPair
    run: Callable
    body: type[BaseModel] | None = None
    change: Change | None = None
    create: bool = False
    absent: Callable[[str], dict] | None = None
    unconfigured: dict | None = None


OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "DELETE",
        "/connection",
        _DISCONNECT,
        lambda client, _: client.disconnect(),
        change=Change.DISCONNECT,
        absent=absent_status,
    ),
    Operation(
        "GET",
        "/connection",
        _VIEW,
        lambda client, _: client.refresh(),
        absent=absent_status,
        unconfigured=_UNCONFIGURED_CONNECTION,
    ),
    Operation(
        "GET",
        "/connection/status",
        _VIEW,
        lambda client, _: client.status(),
        absent=absent_status,
        unconfigured=_UNCONFIGURED_CONNECTION,
    ),
    Operation(
        "POST",
        "/enrollment/start",
        _ENROLL,
        lambda client, _: client.start(),
        change=Change.ENROLLMENT_START,
        create=True,
    ),
    Operation(
        "POST",
        "/enrollment/complete",
        _ENROLL,
        lambda client, body: client.complete(body.state_id),
        EnrollmentConfirmation,
        Change.ENROLLMENT_COMPLETE,
    ),
    Operation(
        "DELETE",
        "/enrollment",
        _ENROLL,
        lambda client, _: client.cancel(),
        change=Change.ENROLLMENT_CANCEL,
    ),
    Operation("GET", "/profile", _VIEW, lambda client, _: client.refresh(), absent=absent_status),
    Operation(
        "PATCH",
        "/profile",
        _PROFILE,
        lambda client, body: client.rename(body.display_name, body.operation_id),
        ProfileUpdate,
        Change.PROFILE_RENAME,
    ),
    Operation(
        "DELETE",
        "/profile",
        _PROFILE,
        lambda client, _: client.cancel_rename(),
        change=Change.PROFILE_RENAME_CANCEL,
    ),
    Operation(
        "POST",
        "/keys/rotate",
        _KEYS,
        lambda client, _: CommunityRotation(client).run(prepare=True),
        RotationConfirmation,
        Change.KEY_ROTATION,
    ),
    Operation(
        "POST",
        "/keys/rotation-status",
        _KEYS,
        lambda client, _: CommunityRotation(client).run(),
    ),
    Operation(
        "POST",
        "/keys/rotation-retry",
        _KEYS,
        lambda client, _: CommunityRotation(client).run(resend=True),
        RotationConfirmation,
        Change.KEY_ROTATION_RETRY,
    ),
    Operation(
        "GET",
        "/keys/recovery",
        _VIEW,
        lambda client, _: CommunityRecovery(client).status(),
        absent=lambda _url: _ABSENT_RECOVERY,
        unconfigured=_UNCONFIGURED_RECOVERY,
    ),
    Operation(
        "POST",
        "/keys/recovery/start",
        _KEYS,
        lambda client, body: CommunityRecovery(client).start(body.old_key_id),
        RecoveryStart,
        Change.KEY_RECOVERY_START,
    ),
    Operation(
        "POST",
        "/keys/recovery/complete",
        _KEYS,
        lambda client, body: CommunityRecovery(client).run(state_id=body.state_id, resend=True),
        EnrollmentConfirmation,
        Change.KEY_RECOVERY_COMPLETE,
    ),
    Operation(
        "POST",
        "/keys/recovery/check",
        _KEYS,
        lambda client, _: CommunityRecovery(client).run(),
    ),
    Operation(
        "POST",
        "/keys/recovery/retry",
        _KEYS,
        lambda client, _: CommunityRecovery(client).run(resend=True),
        RotationConfirmation,
        Change.KEY_RECOVERY_RETRY,
    ),
)


@dataclass(frozen=True)
class _Refusal:
    status: int
    content: dict
    reason: str


@dataclass(frozen=True)
class _Caller:
    db: Session
    access: PrincipalAccess

    @property
    def own(self) -> AccountRef:
        return AccountRef(self.access.principal.user_id, self.access.principal.account.role)

    def record(self, target: int, outcome: AuditOutcome, details: dict) -> None:
        """Store one identity change or its refusal in its own transaction."""
        self.db.rollback()
        principal = self.access.principal
        record_event(
            self.db,
            AuditEvent(
                auth_policy.session_now(),
                principal.user_id,
                principal.credential,
                AuditAction.COMMUNITY_IDENTITY_CHANGE,
                outcome,
                target_user=UserId(target),
                details=details,
            ),
        )


def _caller(
    response: Response,
    access: PrincipalAccess = Depends(request_access),
    db: Session = Depends(get_db),
) -> _Caller:
    response.headers["Cache-Control"] = "no-store"
    return _Caller(db, access)


def _failure(error: Exception) -> _Refusal:
    if isinstance(error, CommunityFailure):
        status = 503 if error.retryable or error.code == "not_configured" else 409
        reason = "service_unavailable" if status == 503 else "service_rejected"
        return _Refusal(status, {"code": error.code, "retryable": error.retryable}, reason)
    assert isinstance(error, IdentityUnavailable)
    return _Refusal(
        409, {"code": "identity_" + error.status, "retryable": False}, "identity_unavailable"
    )


async def _perform(operation: Operation, caller: _Caller, user_id: int | None, body):
    """Authorize, resolve the account's own identity home, run, and audit one operation."""
    target: AccountRef | None = None
    try:
        target = (
            caller.own
            if user_id is None
            else resolve_account(caller.db, caller.access, user_id, operation.action)
        )
        require(caller.access, operation.action, target)
        if getattr(body, "confirmed", True) is not True:
            return JSONResponse(
                {"code": "confirmation_required", "retryable": False},
                status_code=409,
                headers=_NO_STORE,
            )
        if operation.unconfigured is not None and not (
            settings.community_service_url or settings.community_service_audience
        ):
            return JSONResponse(operation.unconfigured, headers=_NO_STORE)
        transport = service_transport()
        home = user_identity_home(
            caller.db, settings.data_dir, target.user_id, create=operation.create
        )
        if home is None:
            if operation.absent is None:
                raise CommunityFailure("not_connected")
            return JSONResponse(operation.absent(transport.endpoint.url), headers=_NO_STORE)
        result = operation.run(CommunityClient(caller.db, home, transport), body)
        if hasattr(result, "__await__"):
            result = await result
    except AccessDenied as denial:
        account = denial.account if isinstance(denial, HiddenAccount) else target
        if operation.change is not None and account is not None:
            caller.record(
                account.user_id,
                AuditOutcome.DENIED,
                {"reason": denial.code.value, "change": operation.change.value},
            )
        return JSONResponse(
            {"code": denial.code.value, "detail": DENIAL_DETAILS[denial.code]},
            status_code=denial.status_code,
            headers=denial.headers | _NO_STORE,
        )
    except (CommunityFailure, IdentityUnavailable) as error:
        refusal = _failure(error)
        if operation.change is not None:
            caller.record(
                target.user_id,
                AuditOutcome.FAILED,
                {"change": operation.change.value, "reason": refusal.reason},
            )
        return JSONResponse(refusal.content, status_code=refusal.status, headers=_NO_STORE)
    if operation.change is not None:
        caller.record(target.user_id, AuditOutcome.SUCCEEDED, {"change": operation.change.value})
    return JSONResponse(result, headers=_NO_STORE)


def _endpoints(operation: Operation):
    """The caller's own route and the named-account route of one operation."""
    if operation.body is None:

        async def own(caller: _Caller = Depends(_caller)):
            return await _perform(operation, caller, None, None)

        async def named(user_id: int, caller: _Caller = Depends(_caller)):
            return await _perform(operation, caller, user_id, None)

    else:
        model = operation.body

        async def own(body: model, caller: _Caller = Depends(_caller)):  # type: ignore[valid-type]
            return await _perform(operation, caller, None, body)

        async def named(user_id: int, body: model, caller: _Caller = Depends(_caller)):  # type: ignore[valid-type]
            return await _perform(operation, caller, user_id, body)

    return own, named


for _operation in OPERATIONS:
    _own, _named = _endpoints(_operation)
    router.add_api_route(_operation.path, _own, methods=[_operation.method])
    users_router.add_api_route(_operation.path, _named, methods=[_operation.method])
