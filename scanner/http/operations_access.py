"""Capability checks and audit events for instance settings and operations routes.

A handler depends on `operation(capability, audited)`, which loads the live
principal, decides the capability against the instance, and stores a denied
event when the action is audited, all before the body is validated. A
handler that needs a password still decides early with `fresh` and checks the
proof itself with `require_fresh`. `Operator.sees_paths` is the one decision
for server paths in these responses. `Operator.may` shapes responses, and
`stage` adds the success event to the change's own transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.security import auth_policy
from scanner.security.access.api_shapes import AccessErrorCode, Reauthentication
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.request_access import request_access
from scanner.security.access.targets import INSTANCE
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import record_event, stage_event
from scanner.security.audit.variants import operation_variant

from . import reauthentication

Details = Mapping[str, str | int | bool]


@dataclass(frozen=True)
class Operator:
    """The live principal behind one settings request, with its session and clock."""

    access: PrincipalAccess
    db: Session
    now: datetime
    request: Request

    def may(self, capability: Capability) -> bool:
        return isinstance(decide(self.access, capability, INSTANCE), Allowed)

    def sees_paths(self) -> bool:
        """Whether server filesystem paths may appear in this caller's responses.

        Settings paths belong to no media item, so the live `media.paths.view`
        grant decides alone. Every path-bearing settings response asks here.
        """

        return Capability.MEDIA_PATHS_VIEW in self.access.capabilities

    def require(
        self,
        capability: Capability,
        audited: AuditAction | None = None,
        *,
        fresh: bool = False,
        details: Details | None = None,
    ) -> None:
        """Raise `AccessDenied` unless allowed, storing the refusal when `audited`.

        With `fresh`, a caller missing only this request's password passes, so
        the handler can read the proof from its body and call `require_fresh`.
        """

        decision = decide(self.access, capability, INSTANCE)
        if isinstance(decision, Allowed):
            return
        if fresh and decision.code is AccessErrorCode.REAUTHENTICATION_REQUIRED:
            return
        self._refuse(
            audited, decision.code, operation_variant(audited, capability) | dict(details or {})
        )
        raise AccessDenied(decision.code)

    def require_paths(
        self, audited: AuditAction | None = None, *, details: Details | None = None
    ) -> None:
        """Refuse a caller whose response would carry paths it may not see."""

        if not self.sees_paths():
            self._refuse(audited, AccessErrorCode.CAPABILITY_DENIED, details)
            raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)

    def require_fresh(
        self, capability: Capability, proof: Reauthentication | None, audited: AuditAction
    ) -> None:
        """Decide a fresh-interactive action: a session and its password in this request."""

        from . import auth_api

        try:
            reauthentication.reauthentication_guard.authorize(
                self.db,
                self.access,
                capability,
                INSTANCE,
                proof,
                address=auth_api._client_address(self.request).throttle_key,
            )
        except AccessDenied as denial:
            self._refuse(audited, denial.code, operation_variant(audited, capability))
            raise

    def stage(self, action: AuditAction, details: Details, *, resource: str | None = None) -> None:
        """Add the succeeded event to the open transaction; committing the change commits it."""

        stage_event(self.db, self._event(action, AuditOutcome.SUCCEEDED, details, resource))

    def record(self, action: AuditAction, details: Details, *, resource: str | None = None) -> None:
        """Store the succeeded event of a change kept outside the database, after it is made."""

        record_event(self.db, self._event(action, AuditOutcome.SUCCEEDED, details, resource))

    def _refuse(
        self, audited: AuditAction | None, code: AccessErrorCode, details: Details | None = None
    ) -> None:
        if audited is not None:
            event = self._event(
                audited, AuditOutcome.DENIED, dict(details or {}) | {"reason": code.value}, None
            )
            record_event(self.db, event)

    def _event(
        self, action: AuditAction, outcome: AuditOutcome, details: Details, resource: str | None
    ) -> AuditEvent:
        principal = self.access.principal
        return AuditEvent(
            self.now,
            principal.user_id,
            principal.credential,
            action,
            outcome,
            resource=resource,
            details=details,
        )


def operator(
    request: Request,
    access: PrincipalAccess = Depends(request_access),
    db: Session = Depends(get_db),
) -> Operator:
    """FastAPI dependency for a handler that decides its own capability."""

    return Operator(access, db, auth_policy.session_now(), request)


def operation(
    capability: Capability,
    audited: AuditAction | None = None,
    *,
    paths: bool = False,
    fresh: bool = False,
) -> Callable[[Operator], Operator]:
    """FastAPI dependency factory: refuse the request before its body is validated.

    `paths` marks a route whose response is made of server paths, so it also
    needs `Operator.sees_paths`. `fresh` defers only the password to the handler.
    """

    def dependency(caller: Operator = Depends(operator)) -> Operator:
        caller.require(capability, audited, fresh=fresh)
        if paths:
            caller.require_paths(audited, details=operation_variant(audited, capability))
        return caller

    return dependency
