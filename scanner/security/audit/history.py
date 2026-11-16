"""Read audit history under fresh account and media authority."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import MediaItemRow, UserRow
from scanner.persistence.repository.repository_paths import resolve_local_path
from scanner.persistence.repository.scan_jobs import ScanMedia
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import UserId
from scanner.security.access.targets import AccountRef
from scanner.security.access.vocabulary import Role
from scanner.security.access.write_boundary import fresh_authority_write
from scanner.security.jellyfin_accounts.evidence import evidence_cache
from scanner.security.scan_jobs.media import CapturedItems, ScanEvidence, decide_scans
from scanner.security.scan_jobs.state import JobState, load_authority
from scanner.server_paths import UnresolvablePath

from .query import (
    AuditEntry,
    AuditFilters,
    AuditPage,
    candidates,
    require_history,
    scoped_page,
    visible_entries,
)

_NO_TARGET = ScanMedia("", "", None, "")


@dataclass(frozen=True)
class HistoryState:
    authority: JobState
    accounts: tuple[tuple[int, str], ...]
    media: tuple[tuple[str, ScanMedia], ...]


class AuditHistoryBusy(RuntimeError):
    """Visibility could not stabilize within the bounded evidence retries."""


def _state(
    db: Session, access: PrincipalAccess, now: datetime, event_id: str | None
) -> HistoryState:
    principal = access.principal
    authority = load_authority(
        db,
        principal.user_id,
        _NO_TARGET,
        now,
        credential=principal.credential,
        session_id=access.session_id,
    )
    if authority.access is None:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    require_history(authority.access)
    rows = candidates(db, now, event_id)
    keys = {row.media_key for row in rows if row.media_key is not None}
    users = {row.target_user_id for row in rows if row.target_user_id is not None}
    media = []
    for row in db.execute(
        select(
            MediaItemRow.id,
            MediaItemRow.server_id,
            MediaItemRow.jellyfin_id,
            MediaItemRow.library_id,
            MediaItemRow.path,
        )
        .where(MediaItemRow.id.in_(keys))
        .order_by(MediaItemRow.id)
    ):
        try:
            path = resolve_local_path(db, row.path).local_path if row.path else ""
        except UnresolvablePath:
            # Left undecided, so readers other than the owner see its events as unresolved media.
            continue
        media.append((row.id, ScanMedia(row.server_id, row.jellyfin_id, row.library_id, path)))
    return HistoryState(
        authority,
        tuple(
            tuple(row)
            for row in db.execute(
                select(UserRow.id, UserRow.role).where(UserRow.id.in_(users)).order_by(UserRow.id)
            )
        ),
        tuple(media),
    )


def read_history(
    db: Session,
    access: PrincipalAccess,
    filters: AuditFilters,
    *,
    now: datetime,
    event_id: str | None = None,
) -> AuditPage | AuditEntry:
    """Collect provider evidence unlocked, then scope one consistent list or detail read.

    Expiry is checked even when physical retention has not run yet. Nothing is
    cached between requests, and absent, expired, and hidden IDs all return 404.
    """
    require_history(access)

    def state():
        return _state(db, access, now, event_id)

    def prepare():
        before = state()
        current = before.authority.access
        media = [item for _key, item in before.media]
        if not media or current.principal.account.role is Role.OWNER:
            return before, None

        def resolve(path):
            return resolve_local_path(db, path).local_path

        with evidence_cache.evidence(db, current.principal.account, resolve, now) as value:
            items = CapturedItems(value.items)
            evidence = ScanEvidence(value, items)
            decide_scans(db, current.principal.account, media, now, evidence=evidence)
        items.reader = None
        value.items = None
        return before, evidence

    with fresh_authority_write(
        db,
        prepare,
        state,
        AuditHistoryBusy,
    ) as evidence:
        current = state()
        current_access = current.authority.access
        accounts = {
            UserId(user): AccountRef(UserId(user), Role(role)) for user, role in current.accounts
        }
        decisions = (
            decide_scans(
                db,
                current_access.principal.account,
                [item for _key, item in current.media],
                now,
                evidence=evidence,
            )
            if current.media
            else []
        )
        media = dict(zip((key for key, _item in current.media), decisions, strict=True))
        if event_id is not None:
            found = visible_entries(
                candidates(db, now, event_id), current_access, accounts=accounts, media=media
            )
            if not found:
                raise AccessDenied(AccessErrorCode.NOT_FOUND)
            return found[0]
        return scoped_page(db, current_access, filters, accounts=accounts, media=media, now=now)
