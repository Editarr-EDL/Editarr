"""Delete another account after previewing its impact, alone or in bulk (MU-38).

The preview and the deletion compute the same consent under the writer lock, and
the deletion applies only the confirmation the caller saw. A retry of a
completed deletion by the same actor with the same request returns the stored
result; any other request about a deleted account answers 404.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import OperationalError

from scanner.modeling.model_account_deletion import (
    AccountDeletionPreview,
    AccountDeletionRequest,
    AccountDeletionResult,
    BulkAccountDeletionOutcome,
    BulkAccountDeletionRequest,
    BulkAccountDeletionResponse,
    Mode,
)
from scanner.persistence.busy import is_busy
from scanner.persistence.repository.account_impact import Disposition
from scanner.security.access.decisions import AccessDenied

from . import account_operations as operations
from .account_deletion_flow import delete_account as _delete
from .account_deletion_flow import preview_deletion
from .users_access import Caller, Refusal, caller, respond

router = APIRouter(prefix="/api/users", tags=["users"])
_STOPS_BULK = frozenset(
    {"reauthentication_failed", "reauthentication_throttled", "jellyfin_unavailable"}
)


@router.get("/{user_id}/deletion", response_model=AccountDeletionPreview)
def preview_account_deletion(
    user_id: int,
    mode: Mode = Query(...),
    destination: int | None = Query(None, description="The transfer destination's user ID"),
    caller: Caller = Depends(caller),
):
    """Describe what deleting another account in `mode` does to each of its resources."""
    return respond(
        caller, lambda: preview_deletion(caller, user_id, Disposition(mode), destination)
    )


@router.post("/{user_id}/deletion", response_model=AccountDeletionResult)
def delete_account(user_id: int, body: AccountDeletionRequest, caller: Caller = Depends(caller)):
    """Delete another account exactly as previewed; the request carries the caller's password."""
    proofs = operations.Proofs(caller, body.reauthentication)
    return respond(caller, lambda: _delete(caller, proofs, user_id, body))


@router.post("/bulk-deletion", response_model=BulkAccountDeletionResponse)
def bulk_delete_accounts(body: BulkAccountDeletionRequest, caller: Caller = Depends(caller)):
    """Delete each listed account with its own confirmation, committing each alone.

    Every target is authorized and confirmed on its own, and the password is
    verified once. A refusal or an operational failure of one target names its
    code after that target rolls back; earlier targets stay applied and later ones
    still run. A wrong or throttled password stops the request.
    """

    def apply() -> BulkAccountDeletionResponse:
        proofs = operations.Proofs(caller, body.reauthentication)
        results = []
        for target in body.targets:
            try:
                result = _delete(caller, proofs, target.user_id, target)
            except AccessDenied as denial:
                if denial.code.value in _STOPS_BULK:
                    raise
                code = denial.code.value
            except Refusal as refusal:
                code = refusal.code
            except OperationalError as error:
                if not is_busy(error):
                    raise
                code = "database_busy"
            else:
                results.append(
                    BulkAccountDeletionOutcome(
                        user_id=target.user_id, outcome="applied", result=result
                    )
                )
                continue
            caller.db.rollback()
            results.append(
                BulkAccountDeletionOutcome(user_id=target.user_id, outcome="refused", code=code)
            )
        return BulkAccountDeletionResponse(results=results)

    return respond(caller, apply)
