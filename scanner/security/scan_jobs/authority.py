"""Whether a job's initiator may still run it, decided at each worker action boundary.

A job runs with its initiator's live access, never the access it had when
queued. `decide_job` loads the initiator, refuses a disabled account, and asks
the evaluator for `scans.start` on the scan's media. A refused job ends `failed` with a
`REFUSAL_MESSAGES` text and leaves the queue; it is never retried, and a new
scan, authorized afresh, is the retry. Jobs have no quota.

`JobGate` coalesces read-only checks briefly while reloading stored authority.
Its `run` method gathers provider evidence before locking, then validates its
stored state and decides the write inside the transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from time import monotonic

from sqlalchemy.orm import Session, sessionmaker

from scanner.persistence.repository.scan_jobs import scan_job_facts
from scanner.persistence.write_lock import write_lock
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import Allowed, admission_error
from scanner.security.access.evaluator import decide
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.load import load_access
from scanner.security.access.targets import MediaRef
from scanner.security.access.write_boundary import (
    MAX_EVIDENCE_ATTEMPTS as MAX_EVIDENCE_ATTEMPTS,
)
from scanner.security.access.write_boundary import fresh_authority_write
from scanner.security.jellyfin_accounts.evidence import MediaEvidence

from .media import CapturedItems, ScanEvidence, decide_scans, gather_evidence
from .state import JobState, load_state

RECHECK_SECONDS = 0.25


class JobRefusal(StrEnum):
    ACCOUNT_UNAVAILABLE = "account_unavailable"
    CAPABILITY_DENIED = "capability_denied"
    MEDIA_UNAVAILABLE = "media_unavailable"


REFUSAL_MESSAGES = {
    JobRefusal.ACCOUNT_UNAVAILABLE: (
        "Stopped: the account that started this scan is no longer available. "
        "Start a new scan to retry."
    ),
    JobRefusal.CAPABILITY_DENIED: (
        "Stopped: the account that started this scan may no longer run it. "
        "Start a new scan to retry."
    ),
    JobRefusal.MEDIA_UNAVAILABLE: (
        "Stopped: the account that started this scan can no longer reach this media. "
        "Start a new scan to retry."
    ),
}


@dataclass(frozen=True)
class JobAllowed:
    access: PrincipalAccess

    @property
    def may_download_models(self) -> bool:
        return Capability.MODELS_DOWNLOAD in self.access.capabilities


@dataclass(frozen=True)
class JobRefused:
    reason: JobRefusal

    @property
    def message(self) -> str:
        return REFUSAL_MESSAGES[self.reason]


class JobRefusedError(Exception):
    """Raised at a worker action boundary when the initiator may no longer run the job."""

    def __init__(self, refusal: JobRefused):
        self.refusal = refusal
        super().__init__(refusal.message)


def decide_job(
    db: Session,
    scan_id: str,
    *,
    now: datetime,
    evidence: ScanEvidence | None = None,
) -> JobAllowed | JobRefused | None:
    """Decide one job for its initiator now; None when the scan no longer exists."""

    facts = scan_job_facts(db, [scan_id]).get(scan_id)
    if facts is None:
        return None
    if facts.initiator_id is None:
        return JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)
    # A durable job is not a browser sign-in, so session-only limits do not apply.
    access = load_access(db, UserId(facts.initiator_id), CredentialKind.API_KEY, now=now)
    if access is None:
        return JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)
    account = access.principal.account
    if admission_error(account.status) is not None:
        return JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)
    media = decide_scans(db, account, [facts.media], now, evidence=evidence)[0]
    decision = decide(access, Capability.SCANS_START, MediaRef(media, existence_visible=True))
    if isinstance(decision, Allowed):
        return JobAllowed(access)
    if decision.code in {AccessErrorCode.MEDIA_UNAVAILABLE, AccessErrorCode.NOT_FOUND}:
        return JobRefused(JobRefusal.MEDIA_UNAVAILABLE)
    if decision.code is AccessErrorCode.CAPABILITY_DENIED:
        return JobRefused(JobRefusal.CAPABILITY_DENIED)
    return JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)


@dataclass
class JobGate:
    """A worker's recheck of its job's initiator; `require` raises `JobRefusedError`."""

    factory: sessionmaker
    scan_id: str
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    _state: JobState | None = field(default=None, init=False, repr=False)
    _evidence: ScanEvidence | None = field(default=None, init=False, repr=False)
    _checked_at: datetime | None = field(default=None, init=False, repr=False)
    _checked_tick: float = field(default=0, init=False, repr=False)
    _verdict: JobAllowed | None = field(default=None, init=False, repr=False)
    _mutex: RLock = field(default_factory=RLock, init=False, repr=False)

    def _fresh(self, state: JobState | None, now: datetime) -> bool:
        return (
            state == self._state
            and self._evidence is not None
            and self._checked_at is not None
            and 0 <= (now - self._checked_at).total_seconds() < RECHECK_SECONDS
            and 0 <= monotonic() - self._checked_tick < RECHECK_SECONDS
        )

    def _prepare(self, db: Session) -> tuple[JobState | None, ScanEvidence]:
        now = self.clock()
        state = load_state(db, self.scan_id, now)
        if self._evidence is not None and self._fresh(state, now):
            return state, self._evidence
        if state is None or state.access is None:
            evidence = ScanEvidence(MediaEvidence(), CapturedItems(None))
        else:
            evidence = gather_evidence(db, state.access.principal.account, state.media, now)
        self._state, self._evidence = state, evidence
        self._checked_at, self._checked_tick = self.clock(), monotonic()
        self._verdict = None
        return state, evidence

    def _require(
        self, db: Session, evidence: ScanEvidence, *, download: bool = False
    ) -> JobAllowed:
        verdict = decide_job(db, self.scan_id, now=self.clock(), evidence=evidence)
        return self._allowed(verdict, download=download)

    @staticmethod
    def _allowed(verdict: JobAllowed | JobRefused | None, *, download: bool = False) -> JobAllowed:
        if verdict is None:
            verdict = JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)
        if isinstance(verdict, JobRefused):
            raise JobRefusedError(verdict)
        if download and not verdict.may_download_models:
            raise JobRefusedError(JobRefused(JobRefusal.CAPABILITY_DENIED))
        return verdict

    def require(self, *, download: bool = False) -> JobAllowed:
        """Coalesce read-only polls briefly; stored changes invalidate the cached decision."""

        with self._mutex, self.factory() as db:
            state, evidence = self._prepare(db)
            if self._verdict is None:
                self._verdict = self._require(db, evidence)
                if load_state(db, self.scan_id, self.clock()) != state:
                    self._evidence = None
                    self._verdict = None
                    raise JobRefusedError(JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE))
            return self._allowed(self._verdict, download=download)

    def run(self, db: Session, operation: Callable, *args, download: bool = False, **kwargs):
        """Authorize and execute one write using the same locked database session."""

        def prepare():
            with self.factory() as reader:
                return self._prepare(reader)

        def invalidate():
            self._evidence = None

        with self._mutex:
            with fresh_authority_write(
                db,
                prepare,
                lambda: load_state(db, self.scan_id, self.clock()),
                lambda: JobRefusedError(JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)),
                invalidate=invalidate,
                lock=write_lock,
            ) as evidence:
                self._require(db, evidence, download=download)
                return operation(db, *args, **kwargs)
