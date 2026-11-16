"""Stored authority that must still match evidence collected before a worker write."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.integrations.jellyfin_user_snapshot import JellyfinUserSnapshot
from scanner.persistence.repository.scan_jobs import ScanMedia, scan_job_facts
from scanner.persistence.rows.db_auth import AccountSettingsRow, UserProviderIdentityRow
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.load import load_access
from scanner.security.jellyfin_accounts.store import stored_snapshot
from scanner.security.media_access.resolver import Subject
from scanner.security.media_access.service import load_subject


@dataclass(frozen=True)
class JobState:
    """Account, media, policy, and provider read order behind one worker decision."""

    initiator: int | None
    media: ScanMedia
    access: PrincipalAccess | None
    subject: Subject | None
    snapshot: JellyfinUserSnapshot | None
    ordering: tuple[int, int, str | None, datetime | None, int | None] | None


def load_state(db: Session, scan_id: str, now: datetime) -> JobState | None:
    """Read columns afresh, including policy changes without a permission revision."""
    facts = scan_job_facts(db, [scan_id]).get(scan_id)
    if facts is None:
        return None
    return load_authority(db, facts.initiator_id, facts.media, now)


def load_authority(
    db: Session,
    initiator: int | None,
    media: ScanMedia,
    now: datetime,
    *,
    credential: CredentialKind = CredentialKind.API_KEY,
    session_id: int | None = None,
) -> JobState:
    """Read the same stored authority for an existing job or a not-yet-persisted target."""
    access = (
        load_access(db, UserId(initiator), credential, session_id, now=now)
        if initiator is not None
        else None
    )
    account = access.principal.account if access is not None else None
    ordering = db.execute(
        select(
            AccountSettingsRow.jellyfin_read_generation,
            AccountSettingsRow.jellyfin_clock_fence,
            AccountSettingsRow.jellyfin_read_epoch,
            AccountSettingsRow.jellyfin_read_at,
            select(UserProviderIdentityRow.observed_generation)
            .where(UserProviderIdentityRow.user_id == initiator)
            .scalar_subquery(),
        )
    ).one_or_none()
    return JobState(
        initiator,
        media,
        access,
        load_subject(db, account) if account is not None else None,
        stored_snapshot(db, initiator) if account and account.provider else None,
        tuple(ordering) if ordering is not None else None,
    )
