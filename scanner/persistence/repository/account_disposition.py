"""Apply a confirmed account disposition in one commit with its durable record.

Every database change, the removal journals of chosen export files, the
tombstone, and the `account_dispositions` record commit together. A failure or
hard exit before the commit leaves the account and its files as they were, so
the same confirmation can be retried. After the commit the journals reconcile
against committed rows exactly as MU-36 review deletion does. A transfer's new
files are written before this commit and its old files removed after it
(`account_transfer_files`).
"""

import json
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from scanner.db import (
    JellyfinAccountSyncRow,
    MediaItemExportRow,
    SavedReviewRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.persistence.rows.db_account_dispositions import AccountDispositionRow
from scanner.persistence.rows.db_auth import UserAvatarRow, UserProfileRow
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.fresh import FreshAuthentication
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import AccountStatus, DisableCause, UserId
from scanner.security.access.targets import AccountRef, ActionPair, ResourceRef
from scanner.security.access.vocabulary import Role
from scanner.security.access.write_authorization import revalidate
from scanner.security.api_keys import revoke_api_key
from scanner.security.auth_repository_sessions import revoke_user_sessions
from scanner.security.jellyfin_accounts import blocks

from ..write_lock import write_lock
from . import account_transfer as transfers
from .account_consent import Outcome, canonical, result
from .account_impact import (
    AccountDispositionConflict,
    AccountImpact,
    Disposition,
    Visibility,
    account_reviews,
    account_scans,
    build_impact,
    count,
    personal_files,
)
from .export_discovery import export_media, require_export
from .review_access import VIEW, ReviewAccess, review_facts
from .review_deletion import review_file_authority, review_media
from .review_disposition import FileChoice, plan_deletion, stage_deletion
from .scan_deletion import remove_unreferenced_scan
from .scan_jobs import scan_job_facts

SCAN_DELETE = ActionPair(C.SCANS_DELETE_OWN, C.SCANS_DELETE_OTHER)


@dataclass(frozen=True)
class DispositionRequest:
    """What the actor asks for; `preview` accepts a missing fresh-authentication proof."""

    mode: Disposition
    destination_id: int | None = None
    fresh: FreshAuthentication | None = None
    preview: bool = False


def stored_disposition(db: Session, user_id: int) -> AccountDispositionRow | None:
    return db.get(AccountDispositionRow, user_id)


def _revalidate(db: Session, request: PrincipalAccess, target: AccountRef, ask) -> None:
    try:
        revalidate(db, request, C.ACCOUNTS_DELETE_OTHER, target, fresh=ask.fresh)
    except AccessDenied as denial:
        if not (ask.preview and denial.code is AccessErrorCode.REAUTHENTICATION_REQUIRED):
            raise


def _gather(db: Session, user_id: int):
    """Every review's facts and media, and the account's scans, read before the lock."""
    reviews, complete, items = {}, {}, {}
    for review_id in account_reviews(db, user_id):
        facts = review_facts(db, review_id)
        scans = scan_job_facts(db, facts.memberships)
        reviews[review_id] = facts
        items[review_id] = review_media(db, review_id, facts, scans)
        if set(scans) == set(facts.memberships):
            complete[review_id] = (facts, scans)
    owned = scan_job_facts(db, account_scans(db, user_id))
    media = list(
        dict.fromkeys(
            [*(item for values in items.values() for item in values)]
            + [item.media for item in owned.values()]
        )
    )
    return reviews, complete, items, media


@contextmanager
def disposition_gate(
    caller: ReviewAccess,
    request: PrincipalAccess,
    target: AccountRef,
    ask: DispositionRequest,
    unsettled: int = 0,
) -> Iterator[AccountImpact]:
    """Yield the target's impact under the writer lock after revalidating the caller.

    `unsettled` counts the moves an earlier transfer's recovery left open.
    Media evidence for every review, scan, and publication of the account (and,
    for a transfer, the destination's evidence for the reviews' media) is captured
    first; each decision is then made under the lock.
    """
    db = caller.db
    user_id = target.user_id
    reviews, complete, items, media = _gather(db, user_id)
    receiver = None
    if ask.mode is Disposition.TRANSFER:
        transfer_media = list(dict.fromkeys(item for values in items.values() for item in values))
        receiver = transfers.prepare_receiver(db, ask.destination_id, transfer_media)
    with caller.writing(media) if media else write_lock(db) as captured:
        _revalidate(db, request, target, ask)
        evidence = dict(zip(media, captured or (), strict=True))
        current = scan_job_facts(db, account_scans(db, user_id))

        def may_view(review_id: str) -> bool:
            facts = reviews.get(review_id)
            if facts is None or review_facts(db, review_id) != facts:
                return False
            reference = caller.ref(facts, evidence=evidence[facts.media])
            return isinstance(decide(caller.access, VIEW, reference), Allowed)

        def may_see(item) -> bool:
            if item.media not in evidence:
                return False
            decision = caller.media_decision(item.media, evidence=evidence[item.media])
            try:
                caller.require_scan(item, decision, allow_jobs=True)
            except AccessDenied:
                return False
            return True

        reviews_seen = frozenset(filter(may_view, account_reviews(db, user_id)))

        def may_see_export(row) -> bool:
            item = export_media(row)
            if row.review_id not in reviews_seen or item not in evidence:
                return False
            try:
                require_export(caller, row, caller.media_decision(item, evidence=evidence[item]))
            except AccessDenied:
                return False
            return True

        visible = Visibility(
            reviews_seen,
            frozenset(key for key, item in current.items() if may_see(item)),
            frozenset(
                row.id
                for row in db.scalars(
                    select(MediaItemExportRow).where(
                        MediaItemExportRow.id.in_(personal_files(user_id))
                    )
                )
                if may_see_export(row)
            ),
        )

        def plan_review(review_id: str):
            if review_id not in complete:
                return None
            facts, scans = complete[review_id]
            try:
                authorize = review_file_authority(caller, review_id, facts, scans, evidence)
            except AccessDenied:
                return None
            return plan_deletion(db, db.get(SavedReviewRow, review_id), authorize)

        def allow_scan(item) -> bool:
            if item is None or item.media not in evidence or item.initiator_role is None:
                return False
            owner = AccountRef(UserId(item.initiator_id), Role(item.initiator_role))
            decision = caller.media_decision(item.media, evidence=evidence[item.media])
            return isinstance(
                decide(caller.access, SCAN_DELETE, ResourceRef(owner, decision)), Allowed
            )

        def plan_transfer(account_reviews_now):
            account = transfers.destination(db, request, user_id, receiver)
            return transfers.plan_transfer(
                db, user_id, account, receiver, account_reviews_now, items
            )

        yield build_impact(
            db,
            user_id,
            ask.mode,
            visible,
            plan_review=plan_review,
            scan_facts={key: current.get(key) for key in account_scans(db, user_id)},
            allow_scan=allow_scan,
            plan_transfer=plan_transfer,
            unsettled=unsettled,
        )


def _release_credentials(
    db: Session, user_id: int, now: datetime, *, profile: bool, blocked_by: int | None
) -> dict:
    """End every way the account signs in and free its username and Jellyfin identity.

    With `blocked_by`, the released Jellyfin user is blocked first.
    """
    blocked = blocked_by is not None and blocks.block_linked(db, user_id, actor=blocked_by, now=now)
    sessions = revoke_user_sessions(db, user_id, now=now, commit=False)
    key = revoke_api_key(db, user_id, commit=False)
    linked = db.execute(
        delete(UserProviderIdentityRow).where(UserProviderIdentityRow.user_id == user_id)
    ).rowcount
    db.execute(delete(JellyfinAccountSyncRow).where(JellyfinAccountSyncRow.user_id == user_id))
    if profile:
        db.execute(delete(UserProfileRow).where(UserProfileRow.user_id == user_id))
        db.execute(delete(UserAvatarRow).where(UserAvatarRow.user_id == user_id))
    db.execute(
        update(UserRow)
        .where(UserRow.id == user_id)
        .values(
            status=AccountStatus.DISABLED.value,
            disable_cause=DisableCause.LOCAL.value,
            username=None,
            normalized_username=None,
            updated_at=now.replace(tzinfo=None),
            permission_revision=UserRow.permission_revision + 1,
        )
    )
    return {
        "sessions_revoked": sessions,
        "api_key_revoked": key,
        "identity_released": linked > 0,
        "profile_removed": profile,
        "jellyfin_blocked": blocked,
    }


def _choices_for(impact: AccountImpact, choices: Mapping[int, FileChoice]) -> None:
    listed = {file.row_id for plan in impact.plans for file in plan.files}
    if set(choices) != listed:
        raise AccountDispositionConflict(
            422,
            "account_deletion_choice_required",
            "Choose retain or remove for every listed published file",
        )


def _delete(db, impact: AccountImpact, choices, access, journals) -> Outcome:
    retained, removed = [], []
    for plan in impact.plans:
        own = {file.row_id: choices[file.row_id] for file in plan.files}
        staged = stage_deletion(db, plan, own, access, journals)
        retained += staged.retained
        removed += staged.removed
    deleted, kept = [], []
    db.flush()
    for scan in impact.scans:
        (deleted if remove_unreferenced_scan(db, scan.scan_id) else kept).append(scan.scan_id)
    return Outcome(
        reviews_deleted=tuple(plan.review_id for plan in impact.plans),
        files_retained=tuple(sorted(retained)),
        files_removed=tuple(sorted(removed)),
        scans_deleted=tuple(deleted),
        scans_kept=tuple(kept),
    )


def apply_disposition(
    db: Session,
    access: PrincipalAccess,
    impact: AccountImpact,
    consent: dict,
    choices: Mapping[int, FileChoice],
    *,
    now: datetime,
    stage_audit,
    block: bool = False,
) -> dict:
    """Stage the disposition `impact` describes and commit it with its record.

    Call inside `disposition_gate` with the confirmed impact and its `consent`
    (the preview body), under `publication_lock` in delete and transfer modes.
    `stage_audit(details)` adds the success event to the transaction. Returns the
    actor's result. `block` keeps a linked account's Jellyfin user out afterwards.
    """
    _choices_for(impact, choices)
    outcome = Outcome()
    transfer = impact.transfer
    with ExitStack() as journals:
        if impact.mode is Disposition.DELETE:
            outcome = _delete(db, impact, choices, access, journals)
        elif impact.mode is Disposition.TRANSFER:
            transfers.stage_transfer(db, transfer, impact.reviews, now)
            outcome = Outcome(
                files_moved=tuple(move.export_id for move in transfer.moves),
                policy_files=transfer.policy_exports,
            )
        credentials = _release_credentials(
            db,
            impact.user_id,
            now,
            profile=impact.mode is Disposition.DELETE,
            blocked_by=access.principal.user_id if block else None,
        )
        db.flush()
        projected, inventory = result(
            impact, outcome, credentials, count(db, personal_files(impact.user_id))
        )
        db.add(
            AccountDispositionRow(
                user_id=impact.user_id,
                actor_id=access.principal.user_id,
                mode=impact.mode.value,
                destination_id=transfer.destination_id if transfer else None,
                confirmation=consent["confirmation"],
                impact_json=canonical(consent),
                result_json=canonical({"result": projected, "inventory": inventory}),
                created_at=now.replace(tzinfo=None),
            )
        )
        details = {
            "mode": impact.mode.value,
            "reviews": len(impact.reviews),
            "files_retained": len(outcome.files_retained),
            "files_removed": len(outcome.files_removed),
            "files_moved": len(outcome.files_moved),
            "policy_files": len(outcome.policy_files),
            "scans_deleted": len(outcome.scans_deleted),
            "scans_kept": len(outcome.scans_kept),
            "sessions_revoked": credentials["sessions_revoked"],
            "key_revoked": credentials["api_key_revoked"],
            "identity_released": credentials["identity_released"],
            "jellyfin_blocked": credentials["jellyfin_blocked"],
        }
        if transfer:
            details["destination_id"] = transfer.destination_id
        stage_audit(details)
        try:
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return projected


def stored_result(row: AccountDispositionRow) -> dict:
    return json.loads(row.result_json)["result"]
