"""Whether the account that started an instance operation may still run it.

`OperationGate` is `JobGate` for work that has no scan row: a model download or
a backup a request started. It carries the initiator and the capability the
request was decided on, and decides both again from stored state on every
check. A disabled account or a lost capability raises `JobRefusedError`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from scanner.persistence.write_lock import write_lock
from scanner.security.access.capability_ids import Capability
from scanner.security.access.decisions import Allowed, admission_error
from scanner.security.access.evaluator import decide
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.load import load_access
from scanner.security.access.targets import INSTANCE

from .authority import JobAllowed, JobGate, JobRefusal, JobRefused


@dataclass(frozen=True)
class OperationGate:
    """An instance operation's recheck of its initiator; `require` raises `JobRefusedError`."""

    factory: sessionmaker
    user_id: UserId
    capability: Capability
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    @classmethod
    def for_request(
        cls, access: PrincipalAccess, db: Session, capability: Capability
    ) -> OperationGate:
        """Bind the request's principal to the database that authorized it."""

        factory = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
        return cls(factory, access.principal.user_id, capability)

    def _decide(self, db: Session, *, download: bool) -> JobAllowed:
        # Background work is not a browser sign-in, so session-only limits do not apply.
        access = load_access(db, self.user_id, CredentialKind.API_KEY, now=self.clock())
        if access is None or admission_error(access.principal.account.status) is not None:
            verdict: JobAllowed | JobRefused = JobRefused(JobRefusal.ACCOUNT_UNAVAILABLE)
        elif isinstance(decide(access, self.capability, INSTANCE), Allowed):
            verdict = JobAllowed(access)
        else:
            verdict = JobRefused(JobRefusal.CAPABILITY_DENIED)
        return JobGate._allowed(verdict, download=download)

    def require(self, *, download: bool = False) -> JobAllowed:
        with self.factory() as db:
            return self._decide(db, download=download)

    def run(self, db: Session, operation: Callable, *args, download: bool = False, **kwargs):
        """Decide and execute one write under the database write lock, then commit."""

        with write_lock(db):
            self._decide(db, download=download)
            result = operation(db, *args, **kwargs)
            db.commit()
            return result
