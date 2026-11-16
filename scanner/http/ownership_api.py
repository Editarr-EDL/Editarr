"""Ownership transfer (MU-18): `POST /api/users/{user_id}/ownership`.

Only the owner, in a browser session, with its Jellyfin password in the request, may
make another account the owner. The successor must be active and linked to Jellyfin
on the configured server; the former owner becomes an admin. The change runs through
`account_operations.run`, so it holds the write lock, decides again on fresh rows,
and commits with its `account.ownership.transfer` event.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from scanner.security import account_ownership as ownership
from scanner.security.access.api_shapes import Reauthentication
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.targets import AccountRef
from scanner.security.access.vocabulary import Role
from scanner.security.audit.actions import AuditAction

from . import account_operations as operations
from .users_access import Caller, Details, Refusal, caller, respond

router = APIRouter(prefix="/api/users", tags=["users"])

_REFUSALS = {
    ownership.SuccessorRefusal.DISABLED: "The new owner must be an active account",
    ownership.SuccessorRefusal.CREDENTIALS_REQUIRED: (
        "The new owner must be linked to Jellyfin on the configured server"
    ),
}


class OwnershipTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reauthentication: Reauthentication | None = None


class OwnershipTransferred(BaseModel):
    owner_id: int
    former_owner_id: int
    former_owner_role: Literal["admin"] = Role.ADMIN.value


def _transfer(caller: Caller, target: AccountRef, _fresh) -> Details:
    previous = ownership.transfer_ownership(
        caller.db, caller.own.user_id, target.user_id, now=caller.now
    )
    return {"successor_role": previous.value}


TRANSFER = operations.Operation(C.OWNERSHIP_TRANSFER, AuditAction.OWNERSHIP_TRANSFER, _transfer)


@router.post("/{user_id}/ownership", response_model=OwnershipTransferred)
def transfer_ownership(user_id: int, body: OwnershipTransfer, caller: Caller = Depends(caller)):
    """Make another account the owner; the caller becomes an admin.

    Refusals: 409 `own_account`, 409 `successor_disabled`, 409
    `successor_credentials_required`, 409 `permission_revision_stale` when the
    caller's permissions changed during the request, and the access codes.
    """

    def transfer() -> OwnershipTransferred:
        audited = AuditAction.OWNERSHIP_TRANSFER
        target = caller.other(user_id, C.OWNERSHIP_TRANSFER, audited=audited)
        try:
            operations.run(
                caller, TRANSFER, target, operations.Proofs(caller, body.reauthentication)
            )
        except ownership.SuccessorIneligible as ineligible:
            caller.refuse(audited, target.user_id, ineligible.reason.value)
            raise Refusal(409, ineligible.reason.value, _REFUSALS[ineligible.reason]) from None
        return OwnershipTransferred(owner_id=target.user_id, former_owner_id=caller.own.user_id)

    return respond(caller, transfer)
