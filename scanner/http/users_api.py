"""The Users directory, profiles, and avatars.

`/api/users/{user_id}` names any account, the caller's own included: an own
target checks the own capability, another account the `.other` capability and
target-account protection, so an admin manages ordinary users and only the
owner manages admins. Ordinary users read and edit only their own profile and
never list the directory. A linked account's display name and avatar come from
Jellyfin and cannot be edited here; its language can.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from scanner.security import account_profiles
from scanner.security import account_repository as accounts
from scanner.security.access.authorization import may_attempt, require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import UserId
from scanner.security.access.targets import AccountRef, ActionPair
from scanner.security.account_avatars import MAX_UPLOAD_BYTES, AvatarRejected, decode_avatar
from scanner.security.account_links import cleanup as link_cleanup
from scanner.security.account_ownership import successor_refusal
from scanner.security.audit.actions import AuditAction

from . import account_operations as operations
from .users_access import Caller, Refusal, caller, respond
from .users_api_models import (
    AccountDetail,
    AccountPage,
    AccountSummary,
    ProfileFields,
)

router = APIRouter(prefix="/api/users", tags=["users"])

_EDIT = ActionPair(C.PROFILE_EDIT_OWN, C.ACCOUNTS_PROFILE_EDIT_OTHER)
_VALIDATORS = {
    "display_name": account_profiles.display_name,
    "language": account_profiles.language,
}


def profile_values(body: ProfileFields, names) -> dict[str, str | None]:
    """Validated values for the named profile fields; a `null` stays a clear."""

    values = {}
    for name in names:
        value = getattr(body, name)
        values[name] = None if value is None else _VALIDATORS[name](value)
    return values


def _viewed(caller: Caller, user_id: int) -> AccountRef:
    """Authorize reading one account: the caller's own profile, or the directory."""

    if user_id != caller.own.user_id:
        require(caller.access, C.ACCOUNTS_LIST)
        return caller.account(user_id, C.ACCOUNTS_LIST)
    require(caller.access, C.PROFILE_VIEW_OWN, caller.own)
    return caller.own


def read_detail(caller: Caller, user_id: int) -> AccountDetail:
    record = accounts.read_account(caller.db, user_id)
    if record is None:  # Deleted after authorization.
        raise Refusal(404, "not_found", "Account not found")
    transferable = user_id != caller.own.user_id and may_attempt(
        caller.access, C.OWNERSHIP_TRANSFER, AccountRef(UserId(user_id), record.role)
    )
    return AccountDetail.of(
        record,
        link_cleanup.read(caller.db, user_id),
        successor_refusal(caller.db, user_id) if transferable else None,
    )


@router.get("", response_model=AccountPage)
def list_users(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: str | None = Query(None, max_length=100),
    caller: Caller = Depends(caller),
):
    """One page of the directory with the approved columns, for admins and the owner."""

    def page() -> AccountPage:
        require(caller.access, C.ACCOUNTS_LIST)
        total, records = accounts.list_accounts(
            caller.db, offset=offset, limit=limit, search=search
        )
        return AccountPage(
            total=total,
            offset=offset,
            limit=limit,
            items=[AccountSummary.of(record) for record in records],
        )

    return respond(caller, page)


@router.get("/{user_id}", response_model=AccountDetail)
def read_user(user_id: int, caller: Caller = Depends(caller)):
    """One account's profile: the caller's own, or any account for directory readers."""

    return respond(caller, lambda: read_detail(caller, _viewed(caller, user_id).user_id))


@router.patch("/{user_id}", response_model=AccountDetail)
def edit_user(user_id: int, body: ProfileFields, caller: Caller = Depends(caller)):
    """Change the listed profile fields."""

    def edit() -> AccountDetail:
        target = caller.account(user_id, _EDIT, audited=AuditAction.ACCOUNT_UPDATE)
        caller.authorize(AuditAction.ACCOUNT_UPDATE, _EDIT, target)
        names = body.model_fields_set
        changes = profile_values(body, names & set(accounts.PROFILE_FIELDS))
        with operations.locked(caller, _EDIT, target, AuditAction.ACCOUNT_UPDATE) as target:
            synced = names & set(accounts.JELLYFIN_PROFILE_FIELDS)
            if synced and accounts.is_linked(caller.db, target.user_id):
                raise Refusal(409, "linked_profile_field", "Jellyfin provides this account's name")
            if changes:
                accounts.update_profile(caller.db, target.user_id, changes, now=caller.now)
            caller.stage(AuditAction.ACCOUNT_UPDATE, target.user_id, {"change": "profile"})
            caller.db.commit()
        return read_detail(caller, target.user_id)

    return respond(caller, edit)


async def _upload(request: Request) -> bytes | None:
    """The request body up to the avatar limit, or None when it is larger."""

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        return None
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > MAX_UPLOAD_BYTES:
            return None
    return bytes(received)


def _avatar_target(caller: Caller, user_id: int) -> AccountRef:
    target = caller.account(user_id, _EDIT, audited=AuditAction.ACCOUNT_UPDATE)
    caller.authorize(AuditAction.ACCOUNT_UPDATE, _EDIT, target)
    return target


def _local_avatar(caller: Caller, target: AccountRef) -> None:
    if accounts.is_linked(caller.db, target.user_id):
        raise Refusal(409, "linked_profile_field", "Jellyfin provides this account's avatar")


@router.put("/{user_id}/avatar", response_model=AccountDetail)
def upload_avatar(
    user_id: int, upload: bytes | None = Depends(_upload), caller: Caller = Depends(caller)
):
    """Replace a local account's avatar with an uploaded PNG, JPEG, WebP, or GIF image."""

    def store() -> AccountDetail:
        target = _avatar_target(caller, user_id)
        if upload is None:
            raise AvatarRejected("avatar_too_large", "Avatar images must be 5 MiB or smaller")
        avatar = decode_avatar(upload)
        with operations.locked(caller, _EDIT, target, AuditAction.ACCOUNT_UPDATE) as target:
            _local_avatar(caller, target)
            accounts.set_avatar(caller.db, target.user_id, avatar, now=caller.now)
            caller.stage(AuditAction.ACCOUNT_UPDATE, target.user_id, {"change": "avatar_set"})
            caller.db.commit()
        return read_detail(caller, target.user_id)

    return respond(caller, store)


@router.delete("/{user_id}/avatar", response_model=AccountDetail)
def remove_avatar(user_id: int, caller: Caller = Depends(caller)):
    """Remove a local account's avatar."""

    def remove() -> AccountDetail:
        target = _avatar_target(caller, user_id)
        with operations.locked(caller, _EDIT, target, AuditAction.ACCOUNT_UPDATE) as target:
            _local_avatar(caller, target)
            if accounts.remove_avatar(caller.db, target.user_id):
                caller.stage(
                    AuditAction.ACCOUNT_UPDATE, target.user_id, {"change": "avatar_removed"}
                )
            caller.db.commit()
        return read_detail(caller, target.user_id)

    return respond(caller, remove)


@router.get(
    "/{user_id}/avatar",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def read_avatar(user_id: int, caller: Caller = Depends(caller)):
    """The stored avatar as PNG; its digest is the ETag."""

    def image() -> Response:
        target = _viewed(caller, user_id)
        avatar = accounts.read_avatar(caller.db, target.user_id)
        if avatar is None:
            raise Refusal(404, "avatar_not_found", "This account has no avatar")
        return Response(
            avatar.image,
            media_type="image/png",
            headers={
                "Cache-Control": "private, no-cache",
                "ETag": f'"{avatar.digest}"',
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    return respond(caller, image)
