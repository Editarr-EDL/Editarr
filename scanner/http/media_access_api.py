"""Local media defaults and individual local access.

Every change needs the caller's password in the request and is decided by
MU-11's `media_access.management`: admins manage local scopes while they hold
the scope capabilities. Reads are open to the same callers that may make the change.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from scanner.persistence.repository import media_access as storage
from scanner.persistence.rows.db_policies import MediaSourceKind
from scanner.security import account_repository as accounts
from scanner.security.access.api_shapes import AccessErrorCode, Reauthentication
from scanner.security.access.authorization import may_attempt
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.targets import INSTANCE
from scanner.security.audit.actions import AuditAction
from scanner.security.media_access import management
from scanner.security.media_access.keys import MalformedMediaKey, SourceRef

from . import account_operations as operations
from .users_access import Caller, Refusal, caller, respond

router = APIRouter(tags=["media access"])


class SourceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MediaSourceKind
    id: str = Field(description="A Jellyfin library GUID")


class ScopeBody(BaseModel):
    """A local scope: every source, the listed sources, or (`inherit`) the live default."""

    model_config = ConfigDict(extra="forbid")

    inherit: bool = False
    all_sources: bool = False
    sources: list[SourceBody] = Field(default_factory=list, max_length=256)


class ScopeChange(ScopeBody):
    reauthentication: Reauthentication | None = None


class ScopeView(BaseModel):
    inherited: bool
    all_sources: bool
    sources: list[SourceBody]


class UserMediaAccess(BaseModel):
    """`scope` for a local account; None for a linked one, whose media Jellyfin decides."""

    linked: bool
    scope: ScopeView | None


def scope_choice(body: ScopeBody) -> management.ScopeChoice | None:
    """The stored form of a requested scope; `None` returns an account to the default."""

    if body.inherit:
        if body.all_sources or body.sources:
            raise Refusal(422, "invalid_media_scope", "An inherited scope lists nothing")
        return None
    try:
        sources = frozenset(SourceRef(source.kind, source.id) for source in body.sources)
        return management.ScopeChoice(body.all_sources, sources)
    except (MalformedMediaKey, ValueError) as error:
        raise Refusal(422, "invalid_media_scope", str(error)) from None


def _view(stored: storage.StoredScope | None) -> ScopeView | None:
    if stored is None:
        return None
    return ScopeView(
        inherited=stored.inherited,
        all_sources=stored.all_sources == 1,
        sources=sorted(
            (SourceBody(kind=kind, id=source_id) for kind, source_id in stored.sources),
            key=lambda source: (source.kind.value, source.id),
        ),
    )


def _readable(caller: Caller, capability: C, target=INSTANCE) -> bool:
    return may_attempt(caller.access, capability, target)


@router.get("/api/media-access/default", response_model=ScopeView)
def read_default(caller: Caller = Depends(caller)):
    """The live local default every local account without its own selection follows."""

    def read() -> ScopeView | None:
        if not _readable(caller, C.MEDIA_ACCESS_DEFAULTS_EDIT):
            raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
        return _view(storage.read_scope(caller.db, None))

    return respond(caller, read)


@router.put("/api/media-access/default", response_model=ScopeView)
def change_default(body: ScopeChange, caller: Caller = Depends(caller)):
    """Replace the live local default; it cannot be `inherit`."""

    def change() -> ScopeView | None:
        proofs = operations.Proofs(caller, body.reauthentication)
        fresh = operations.authorized(
            caller, proofs, C.MEDIA_ACCESS_DEFAULTS_EDIT, INSTANCE, AuditAction.MEDIA_ACCESS_CHANGE
        )
        choice = scope_choice(body)
        if choice is None:
            raise Refusal(422, "invalid_media_scope", "The default cannot inherit")
        caller.stage(AuditAction.MEDIA_ACCESS_CHANGE, None, {"change": "default_scope"})
        management.set_default_scope(caller.db, caller.access, choice, fresh=fresh, now=caller.now)
        return _view(storage.read_scope(caller.db, None))

    return respond(caller, change)


@router.get("/api/users/{user_id}/media-access", response_model=UserMediaAccess)
def read_user_access(user_id: int, caller: Caller = Depends(caller)):
    """Another account's own selection, or the default it follows."""

    def read() -> UserMediaAccess:
        target = caller.other(user_id, C.MEDIA_ACCESS_USER_EDIT)
        return _user_access(caller, target)

    return respond(caller, read)


def _user_access(caller: Caller, target) -> UserMediaAccess:
    if not _readable(caller, C.MEDIA_ACCESS_USER_EDIT, target):
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    linked = accounts.is_linked(caller.db, target.user_id)
    return UserMediaAccess(
        linked=linked,
        scope=None if linked else _view(storage.read_scope(caller.db, target.user_id)),
    )


@router.put("/api/users/{user_id}/media-access", response_model=UserMediaAccess)
def change_user_access(user_id: int, body: ScopeChange, caller: Caller = Depends(caller)):
    """Store a local account's own selection, or return it to the default with `inherit`."""

    def change() -> UserMediaAccess:
        target = caller.other(
            user_id, C.MEDIA_ACCESS_USER_EDIT, audited=AuditAction.MEDIA_ACCESS_CHANGE
        )
        operation = operations.media_scope(scope_choice(body))
        operations.run(caller, operation, target, operations.Proofs(caller, body.reauthentication))
        return _user_access(caller, target)

    return respond(caller, change)
