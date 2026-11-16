"""Jellyfin account administration: import, manual sync, and blocked users (MU-16, JF-05).

Every handler decides through the shared evaluator. Jellyfin is read before any
database write, and results are reported per Jellyfin user or per account.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from scanner.formats.export_layout import JellyfinUserId
from scanner.integrations.jellyfin_user_snapshot import (
    InvalidPayload,
    JellyfinUserSnapshot,
    Unverified,
    canonical_guid,
)
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.persistence.rows.db_auth import (
    AccountSettingsRow,
    JellyfinAccountSyncRow,
    UserProviderIdentityRow,
)
from scanner.security import auth_policy
from scanner.security.access.api_shapes import Reauthentication
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import JellyfinServerId, ProviderIdentity, ProviderLinkState
from scanner.security.access.link_state import VERIFICATION_GRACE, effective_link_state
from scanner.security.access.records import AuditOutcome
from scanner.security.access.targets import INSTANCE
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import record_event, stage_event
from scanner.security.jellyfin_accounts import admission, blocks, reads, store
from scanner.security.jellyfin_accounts.sync import (
    SyncInProgress,
    SyncRun,
    SyncTrigger,
    open_adapter,
    registry,
)

from . import account_operations as operations
from .users_access import Caller, Refusal, caller, respond

router = APIRouter(tags=["jellyfin accounts"])

IMPORT_LIMIT = 1000


class JellyfinCandidate(BaseModel):
    jellyfin_user_id: str
    name: str
    is_administrator: bool
    is_disabled: bool
    user_id: int | None
    """The linked Editarr account, if this Jellyfin user already has one."""
    blocked: bool = False
    """Kept out when their account was deleted; import refuses them until unblocked."""


class JellyfinCandidates(BaseModel):
    users: list[JellyfinCandidate]


class ImportRequest(BaseModel):
    """Exactly one of `all: true` or a nonempty `user_ids` list."""

    all: bool = False
    user_ids: list[str] | None = Field(default=None, max_length=IMPORT_LIMIT)

    @field_validator("user_ids")
    @classmethod
    def _canonical(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        try:
            return [canonical_guid(item) for item in value]
        except InvalidPayload as error:
            raise ValueError("Jellyfin user IDs must be GUIDs") from error

    @model_validator(mode="after")
    def _one_selection(self) -> ImportRequest:
        if self.all == bool(self.user_ids):
            raise ValueError("Choose all users or a nonempty list of user IDs")
        return self


class ImportResultModel(BaseModel):
    jellyfin_user_id: str
    name: str | None
    outcome: admission.ImportOutcome
    user_id: int | None


class ImportResponse(BaseModel):
    results: list[ImportResultModel]


class SyncResultModel(BaseModel):
    user_id: int
    outcome: str
    link_state: ProviderLinkState
    role: str
    status: str
    failure: str | None


class SyncRunModel(BaseModel):
    trigger: SyncTrigger
    started_at: datetime
    finished_at: datetime
    counts: dict[str, int]
    results: list[SyncResultModel]


class LinkedAccountStatus(BaseModel):
    user_id: int
    jellyfin_user_id: str
    name: str | None
    link_state: ProviderLinkState
    """The effective state now: verified access expires 24 hours after `last_verified_at`."""
    upstream_state: str | None
    last_verified_at: datetime | None
    grace_expires_at: datetime | None
    last_attempt_at: datetime | None
    last_outcome: str | None
    last_failure: str | None


class SyncStatus(BaseModel):
    running: bool
    last_run: SyncRunModel | None
    accounts: list[LinkedAccountStatus]


def _run_model(run: SyncRun) -> SyncRunModel:
    return SyncRunModel(
        trigger=run.trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        counts=run.counts(),
        results=[
            SyncResultModel(
                user_id=result.user_id,
                outcome=result.outcome.value,
                link_state=result.link_state,
                role=result.role.value,
                status=result.status.value,
                failure=result.failure,
            )
            for result in run.results
        ],
    )


def _listing(caller: Caller) -> tuple[JellyfinUserAdapter, tuple[JellyfinUserSnapshot, ...]]:
    adapter = open_adapter(caller.db)
    if adapter is None:
        raise Refusal(409, "jellyfin_not_configured", "Connect Jellyfin first")
    listing = adapter.users()
    if isinstance(listing, Unverified):
        adapter.close()
        raise Refusal(503, "jellyfin_unavailable", "Jellyfin could not list its users", True)
    return adapter, listing


@router.get("/api/jellyfin/users", response_model=JellyfinCandidates)
def list_jellyfin_users(caller: Caller = Depends(caller)):
    """Jellyfin users that may be imported, with any linked account."""

    def read() -> JellyfinCandidates:
        require(caller.access, C.ACCOUNTS_JELLYFIN_IMPORT)
        adapter, listing = _listing(caller)
        adapter.close()
        users = []
        for snapshot in listing:
            users.append(
                JellyfinCandidate(
                    jellyfin_user_id=snapshot.identity.user_id.value,
                    name=snapshot.name,
                    is_administrator=snapshot.is_administrator,
                    is_disabled=snapshot.is_disabled,
                    user_id=store.find_identity(caller.db, snapshot.identity),
                    blocked=blocks.is_blocked(caller.db, snapshot.identity),
                )
            )
        return JellyfinCandidates(users=users)

    return respond(caller, read)


@router.post("/api/jellyfin/users/import", response_model=ImportResponse)
def import_jellyfin_users(body: ImportRequest, caller: Caller = Depends(caller)):
    """Create accounts for the selected or all Jellyfin users; report each one."""

    def change() -> ImportResponse:
        caller.authorize(AuditAction.ACCOUNT_CREATE, C.ACCOUNTS_JELLYFIN_IMPORT, INSTANCE)
        read = reads.begin_read(caller.db, auth_policy.session_now)
        adapter, listing = _listing(caller)
        principal = caller.access.principal
        with adapter:
            try:
                results = admission.import_users(
                    caller.db,
                    adapter,
                    listing,
                    None if body.all else frozenset(body.user_ids),
                    actor=principal.user_id,
                    credential=principal.credential,
                    read=read,
                )
            except admission.ListingSuperseded:
                raise Refusal(
                    503, "jellyfin_unavailable", "The import was superseded; retry it", True
                ) from None
        return ImportResponse(results=[ImportResultModel(**vars(result)) for result in results])

    return respond(caller, change)


@router.post("/api/jellyfin/sync", response_model=SyncRunModel)
def sync_jellyfin_accounts(caller: Caller = Depends(caller)):
    """Refresh every linked account now and return each account's result."""

    def change() -> SyncRunModel:
        caller.authorize(AuditAction.JELLYFIN_SYNC, C.ACCOUNTS_JELLYFIN_SYNC, INSTANCE)
        try:
            run = registry.run(caller.db, SyncTrigger.MANUAL, clock=auth_policy.session_now)
        except SyncInProgress:
            raise Refusal(409, "sync_in_progress", "A Jellyfin sync is already running") from None
        caller.stage(
            AuditAction.JELLYFIN_SYNC, None, {**run.counts(), "accounts": len(run.results)}
        )
        caller.db.commit()
        return _run_model(run)

    return respond(caller, change)


def _account_status(row, now: datetime, last_read_at: datetime | None) -> LinkedAccountStatus:
    identity, sync = row.UserProviderIdentityRow, row.JellyfinAccountSyncRow
    verified = store.utc(identity.last_verified_at)
    stored = ProviderLinkState(identity.link_state)
    return LinkedAccountStatus(
        user_id=identity.user_id,
        jellyfin_user_id=identity.provider_user_id,
        name=sync.jellyfin_name if sync else None,
        link_state=effective_link_state(stored, verified, now, last_read_at=last_read_at),
        upstream_state=sync.upstream_state if sync else None,
        last_verified_at=verified,
        grace_expires_at=verified + VERIFICATION_GRACE if verified else None,
        last_attempt_at=store.utc(sync.last_attempt_at) if sync else None,
        last_outcome=sync.last_outcome if sync else None,
        last_failure=sync.last_failure if sync else None,
    )


@router.get("/api/jellyfin/sync", response_model=SyncStatus)
def read_jellyfin_sync(caller: Caller = Depends(caller)):
    """The last sync run and every linked account's verification state."""

    def read() -> SyncStatus:
        require(caller.access, C.ACCOUNTS_JELLYFIN_SYNC)
        rows = caller.db.execute(
            select(UserProviderIdentityRow, JellyfinAccountSyncRow)
            .outerjoin(
                JellyfinAccountSyncRow,
                JellyfinAccountSyncRow.user_id == UserProviderIdentityRow.user_id,
            )
            .order_by(UserProviderIdentityRow.user_id)
        ).all()
        last = registry.last_run
        last_read_at = store.utc(caller.db.scalar(select(AccountSettingsRow.jellyfin_read_at)))
        # Judged after the reads, so a Jellyfin read that began since the request started counts.
        now = auth_policy.session_now()
        return SyncStatus(
            running=registry.running,
            last_run=_run_model(last) if last is not None else None,
            accounts=[_account_status(row, now, last_read_at) for row in rows],
        )

    return respond(caller, read)


class BlockedJellyfinUser(BaseModel):
    server_id: str
    jellyfin_user_id: str
    name: str | None
    """Jellyfin's name for the user when their account was deleted."""
    blocked_by: int | None
    blocked_at: datetime


class BlockedJellyfinUsers(BaseModel):
    blocks: list[BlockedJellyfinUser]


class UnblockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: str = Field(min_length=1, max_length=256)
    jellyfin_user_id: str
    reauthentication: Reauthentication | None = None

    @field_validator("server_id", "jellyfin_user_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        try:
            return canonical_guid(value)
        except InvalidPayload as error:
            raise ValueError("Jellyfin server and user IDs must be GUIDs") from error


def _blocked(caller: Caller) -> BlockedJellyfinUsers:
    return BlockedJellyfinUsers(
        blocks=[
            BlockedJellyfinUser(
                server_id=block.identity.server_id,
                jellyfin_user_id=block.identity.user_id.value,
                name=block.name,
                blocked_by=block.blocked_by,
                blocked_at=block.blocked_at,
            )
            for block in blocks.blocked_users(caller.db)
        ]
    )


@router.get("/api/jellyfin/blocks", response_model=BlockedJellyfinUsers)
def list_blocked_jellyfin_users(caller: Caller = Depends(caller)):
    """Jellyfin users kept out when their accounts were deleted, newest first."""

    def read() -> BlockedJellyfinUsers:
        require(caller.access, C.ACCOUNTS_LIST)
        return _blocked(caller)

    return respond(caller, read)


@router.post("/api/jellyfin/blocks/unblock", response_model=BlockedJellyfinUsers)
def unblock_jellyfin_user(body: UnblockRequest, caller: Caller = Depends(caller)):
    """Let a blocked Jellyfin user sign in or be imported again; their next sign-in
    creates a fresh account."""

    def change() -> BlockedJellyfinUsers:
        audited = AuditAction.JELLYFIN_UNBLOCK
        proofs = operations.Proofs(caller, body.reauthentication)
        fresh = operations.authorized(
            caller, proofs, C.ACCOUNTS_JELLYFIN_UNBLOCK, INSTANCE, audited
        )
        identity = ProviderIdentity(
            JellyfinServerId(body.server_id), JellyfinUserId(body.jellyfin_user_id)
        )
        resource = f"{body.server_id}:{body.jellyfin_user_id}"
        with operations.locked(caller, C.ACCOUNTS_JELLYFIN_UNBLOCK, INSTANCE, audited, fresh=fresh):
            removed = blocks.unblock(caller.db, identity)
            if not removed:
                caller.db.rollback()
                record_event(
                    caller.db,
                    replace(
                        caller.event(
                            audited,
                            None,
                            {"reason": "jellyfin_user_not_blocked"},
                            AuditOutcome.DENIED,
                        ),
                        resource=resource,
                    ),
                )
                raise Refusal(404, "jellyfin_user_not_blocked", "That Jellyfin user is not blocked")
            stage_event(
                caller.db,
                replace(caller.event(audited, None, {}), resource=resource),
            )
            caller.db.commit()
        return _blocked(caller)

    return respond(caller, change)
