"""Proof-bound linking of an account to a Jellyfin identity, including server relinking.

`POST /api/users/{user_id}/link/proof` checks the caller's Jellyfin password and a
Jellyfin sign-in for the identity to link. The target account's ownership rests on
its `ProofBasis`: the caller's fresh authentication for its own account, and the
caller's attestation for any other account. It returns a single-use proof and the
impact preview.
`POST /api/users/{user_id}/link` spends the proof against the preview's
confirmation and moves the account and its personal exports to the new identity;
an account disabled for having no link is enabled. Matching names never link
anything. Wrong Jellyfin passwords are throttled per target and client address and
audited without their values.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from scanner.integrations.jellyfin_user_snapshot import (
    JellyfinUserSnapshot,
    Unverified,
    UnverifiedReason,
)
from scanner.persistence.repository import account_transfer_files as transfer_files
from scanner.persistence.repository.export_publication import ExportConflict
from scanner.persistence.repository.export_recovery import (
    ExportRecoveryRequired,
    publication_lock,
)
from scanner.persistence.rows.db_account_links import RollbackReason
from scanner.security.access import link_state
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.targets import AccountRef, ActionPair
from scanner.security.account_links import transition
from scanner.security.account_links.plan import LinkPlan, build_plan
from scanner.security.account_links.proofs import (
    PROOF_LIFETIME,
    LinkProof,
    ProofBasis,
    ProofRejected,
    proof_basis,
    proofs,
    read_binding,
)
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import record_event
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.admission import encodable, fetch_avatar
from scanner.security.jellyfin_accounts.sync import open_adapter

from . import account_operations as operations
from .account_link_models import (
    LinkPreview,
    LinkProofRequest,
    LinkProofResponse,
    LinkRequest,
    LinkResult,
)
from .auth_rate_limit import LoginRateLimiter
from .users_access import Caller, Refusal, caller, respond

router = APIRouter(prefix="/api/users", tags=["users"])
LINK = ActionPair(C.ACCOUNTS_LINK_OWN, C.ACCOUNTS_LINK_OTHER)
AUDITED = AuditAction.ACCOUNT_LINK
link_proof_limiter = LoginRateLimiter()


class PreviewRefusal(Exception):
    """A 409 that carries the current preview so the caller can review and confirm again."""

    def __init__(self, code: str, detail: str, plan: LinkPlan, basis: ProofBasis) -> None:
        super().__init__(code)
        self.code, self.detail, self.plan, self.basis = code, detail, plan, basis


def _now() -> datetime:
    return link_state.link_clock()


def _target(caller: Caller, user_id: int) -> AccountRef:
    return caller.account(user_id, LINK, audited=AUDITED)


def _refuse(caller: Caller, target: int, code: str, step: str, basis: ProofBasis | None) -> None:
    details = {"step": step} | ({"basis": basis.value} if basis else {})
    caller.refuse(AUDITED, target, code, details=details)


def _jellyfin_snapshot(caller: Caller, username: str, password: str) -> JellyfinUserSnapshot:
    if not encodable(username, password):
        raise Refusal(422, "jellyfin_proof_failed", "Jellyfin did not accept these credentials")
    adapter = open_adapter(caller.db)
    if adapter is None:
        raise Refusal(503, "jellyfin_unavailable", "Jellyfin is not configured", True)
    with adapter:
        proof = adapter.authenticate(username, password)
    if isinstance(proof, Unverified):
        if proof.reason is UnverifiedReason.CREDENTIALS_REJECTED:
            raise Refusal(422, "jellyfin_proof_failed", "Jellyfin did not accept these credentials")
        raise Refusal(503, "jellyfin_unavailable", "Jellyfin did not answer; retry", True)
    return proof.user


def _throttle(caller: Caller, target: int) -> str:
    key = f"link:{target}"
    retry_after = link_proof_limiter.retry_after(username=key, address=caller.address)
    if retry_after:
        _refuse(caller, target, "link_proof_invalid", "proof", None)
        raise Refusal(429, "link_throttled", "Too many failed proofs; wait and retry", True)
    return key


@router.post("/{user_id}/link/proof", response_model=LinkProofResponse)
def prove_link(user_id: int, body: LinkProofRequest, caller: Caller = Depends(caller)):
    """Prove the target account and a Jellyfin identity; return a proof and the preview.

    The proof expires after ten minutes, works once, and only for this caller's
    session and this account.
    """

    def prove() -> LinkProofResponse:
        target = _target(caller, user_id)
        caller.authorize(AUDITED, LINK, target, body.reauthentication)
        actor = int(caller.own.user_id)
        key = _throttle(caller, target.user_id)
        try:
            snapshot = _jellyfin_snapshot(
                caller, body.jellyfin.username, body.jellyfin.password.get_secret_value()
            )
        except Refusal as refusal:
            if refusal.code == "jellyfin_proof_failed":
                link_proof_limiter.record_failure(username=key, address=caller.address)
            _refuse(caller, target.user_id, refusal.code, "proof", None)
            raise
        basis = proof_basis(actor, target.user_id)
        link_proof_limiter.clear_username(key)
        if transfer_files.unsettled(caller.db, target.user_id):
            with publication_lock(caller.db):
                transfer_files.recover(caller.db, user_id=target.user_id)
        plan = build_plan(caller.db, target.user_id, snapshot)
        binding = read_binding(caller.db, target.user_id, snapshot.identity)
        now = _now()
        proof = LinkProof(
            actor_id=actor,
            session_id=caller.access.session_id,
            target_user_id=target.user_id,
            basis=basis,
            binding=binding,
            expires_at=now + PROOF_LIFETIME,
        )
        caller.db.rollback()
        token = proofs.mint(proof, now)
        details = {"step": "proof", "basis": basis.value}
        record_event(caller.db, caller.event(AUDITED, target.user_id, details))
        if plan.blockers:
            _refuse(caller, target.user_id, "link_blocked", "proof", basis)
        return LinkProofResponse(
            proof=token,
            expires_at=proof.expires_at,
            preview=LinkPreview.model_validate(plan.preview(basis)),
            confirmation=plan.confirmation(basis),
        )

    return respond(caller, prove)


def _fresh_snapshot(caller: Caller, proof: LinkProof):
    """Read the proven identity again just before linking, in a numbered Jellyfin read."""
    adapter = open_adapter(caller.db)
    if adapter is None:
        raise Refusal(503, "jellyfin_unavailable", "Jellyfin is not configured", True)
    read = reads.begin_read(caller.db, link_state.link_clock)
    with adapter:
        evidence = adapter.verify_user(proof.identity)
        if isinstance(evidence, Unverified):
            raise Refusal(503, "jellyfin_unavailable", "Jellyfin did not answer; retry", True)
        if not isinstance(evidence, JellyfinUserSnapshot) or evidence.is_disabled:
            raise Refusal(409, "jellyfin_user_unavailable", "The Jellyfin user is unavailable")
        avatar = (
            fetch_avatar(adapter, evidence, None)
            if evidence.avatar_tag is not None
            else store.AvatarChange(None, None)
        )
    return evidence, read, avatar


def _spend(caller: Caller, token: str, target: int, plan: LinkPlan, confirmation: str):
    """Under the write lock: refuse a stale, blocked, or changed link, then spend the proof."""
    now = _now()
    bound = {
        "actor_id": int(caller.own.user_id),
        "session_id": caller.access.session_id,
        "target_user_id": target,
        "now": now,
    }
    proof = proofs.check(token, **bound)
    if read_binding(caller.db, target, plan.after) != proof.binding:
        proofs.take(token, **bound)
        raise ProofRejected("link_proof_stale")
    if plan.blockers:
        raise PreviewRefusal(
            "link_blocked", "Resolve the preview's blockers first", plan, proof.basis
        )
    if confirmation != plan.confirmation(proof.basis):
        raise PreviewRefusal(
            "link_preview_stale", "Review the current preview and confirm", plan, proof.basis
        )
    return proofs.take(token, **bound)


def _execute(
    caller: Caller, body: LinkRequest, target: AccountRef, fresh, proof: LinkProof
) -> LinkResult:
    db, user_id = caller.db, target.user_id
    audited = {"step": "link", "basis": proof.basis.value}
    try:
        snapshot, read, avatar = _fresh_snapshot(caller, proof)
    except Refusal as refusal:
        _refuse(caller, user_id, refusal.code, "link", proof.basis)
        raise
    with publication_lock(db):
        transition.recover(db, _now(), user_id=user_id)
        with operations.locked(caller, LINK, target, AUDITED, fresh=fresh, details=audited):
            plan = build_plan(db, user_id, snapshot)
            proof = _spend(caller, body.proof, user_id, plan, body.confirmation)
            transition_id = transition.begin(db, plan, int(caller.own.user_id), _now()).id
            db.commit()
        try:
            transition.write_files(db, transition_id, plan)
        except (OSError, ValueError, ExportConflict) as error:
            transition.roll_back(db, transition_id, RollbackReason.WRITE_FAILED, _now())
            code = error.code if isinstance(error, ExportConflict) else "write_failed"
            _refuse(caller, user_id, "write_failed", "link", proof.basis)
            raise Refusal(409, code, "A new export could not be written; nothing changed")
        try:
            with operations.locked(caller, LINK, target, AUDITED, fresh=fresh, details=audited):
                result = transition.switch(
                    db, transition_id, plan, snapshot, read, avatar, proof.binding
                )
                details = {
                    "step": "link",
                    "basis": proof.basis.value,
                    "kind": plan.kind.value,
                    "state": "removing",
                    "exports": len(plan.moves),
                    "role": result.role.value,
                }
                caller.stage(AUDITED, user_id, details)
                db.commit()
        except OperationalError:
            if transition.committed_state(db, transition_id) != "removing":
                transition.roll_back(db, transition_id, RollbackReason.WRITE_FAILED, _now())
                raise
        except (transition.LinkRefused, AccessDenied):
            db.rollback()
            transition.roll_back(db, transition_id, RollbackReason.REFUSED, _now())
            raise
        except BaseException:
            db.rollback()
            transition.roll_back(db, transition_id, RollbackReason.WRITE_FAILED, _now())
            raise
        completed = transition.finish(db, transition_id, _now())
    preview = LinkPreview.model_validate(plan.preview(proof.basis))
    return LinkResult(state="completed" if completed else "removing", preview=preview)


def _preview_response(refusal: PreviewRefusal) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "code": refusal.code,
            "detail": refusal.detail,
            "preview": LinkPreview.model_validate(refusal.plan.preview(refusal.basis)).model_dump(
                mode="json"
            ),
            "confirmation": refusal.plan.confirmation(refusal.basis),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{user_id}/link", response_model=LinkResult)
def link_account(user_id: int, body: LinkRequest, caller: Caller = Depends(caller)):
    """Link or relink an account with a proof from `/link/proof` and its confirmation.

    New export files are written first; the identity and publications change in
    one transaction; old files are removed only after that commits. A failure
    before the commit leaves the old identity and files. `removing` reports old
    files that recovery removes later.
    """

    def link() -> LinkResult | JSONResponse:
        target = _target(caller, user_id)
        proven = operations.Proofs(caller, body.reauthentication)
        fresh = operations.authorized(caller, proven, LINK, target, AUDITED)
        basis = None
        try:
            proof = proofs.check(
                body.proof,
                actor_id=int(caller.own.user_id),
                session_id=caller.access.session_id,
                target_user_id=target.user_id,
                now=_now(),
            )
            basis = proof.basis
            return _execute(caller, body, target, fresh, proof)
        except ProofRejected as rejected:
            _refuse(caller, target.user_id, rejected.code, "link", basis)
            raise Refusal(409, rejected.code, "Prove both identities again") from None
        except PreviewRefusal as refusal:
            _refuse(caller, target.user_id, refusal.code, "link", refusal.basis)
            return _preview_response(refusal)
        except transition.LinkRefused as refused:
            _refuse(caller, target.user_id, refused.code, "link", basis)
            raise Refusal(refused.status, refused.code, refused.detail, refused.retry) from None
        except ExportRecoveryRequired:
            _refuse(caller, target.user_id, "write_failed", "link", basis)
            raise Refusal(409, "export_recovery_required", "An export needs recovery first")

    return respond(caller, link)
