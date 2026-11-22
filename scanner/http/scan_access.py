"""Who may see and act on scans and scan jobs, for one request.

A handler depends on `scan_caller`. Each scan resolves to a `ResourceRef` whose
owner is its initiator and whose media is decided from the file it reads. A
caller sees a scan when it may view that job, or when the scan is complete and
it may read findings on that media, which keeps shared evidence reusable. A
scan the caller cannot see answers `scan_not_found`. Responses for a caller
without `media.paths.view` carry no server paths.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar

from fastapi import Depends
from sqlalchemy.orm import Session

from scanner.db import get_db
from scanner.models import Scan, ScanQueueItem
from scanner.persistence.repository.activity_ownership import ActivityOwner
from scanner.persistence.repository.repository_catalog import list_scan_queue
from scanner.persistence.repository.scan_jobs import ScanJobFacts, ScanMedia, scan_job_facts
from scanner.persistence.write_lock import write_lock
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import UserId
from scanner.security.access.records import MediaDecision
from scanner.security.access.request_access import request_access
from scanner.security.access.targets import (
    INSTANCE,
    AccountRef,
    Action,
    ActionPair,
    MediaRef,
    ResourceRef,
)
from scanner.security.access.vocabulary import Role
from scanner.security.access.write_boundary import fresh_authority_write
from scanner.security.scan_jobs.media import decide_scans, gather_evidence
from scanner.security.scan_jobs.state import JobState, load_authority

JOB_VIEW = ActionPair(C.SCAN_JOBS_VIEW_OWN, C.SCAN_JOBS_VIEW_OTHER)
JOB_CANCEL = ActionPair(C.SCAN_JOBS_CANCEL_OWN, C.SCAN_JOBS_CANCEL_OTHER)
JOB_RESUME = ActionPair(C.SCAN_JOBS_RESUME_OWN, C.SCAN_JOBS_RESUME_OTHER)
SCAN_RENAME = ActionPair(C.SCANS_RENAME_OWN, C.SCANS_RENAME_OTHER)
SCAN_DELETE = ActionPair(C.SCANS_DELETE_OWN, C.SCANS_DELETE_OTHER)
REVIEW_VIEW = ActionPair(C.REVIEWS_VIEW_OWN, C.REVIEWS_VIEW_OTHER)


_SAFE_PROGRESS = {
    "queued": "Waiting to scan",
    "acquiring_model": "Acquiring scan model",
    "hashing": "Identifying media",
    "audio": "Analyzing audio",
    "transcription": "Transcribing audio",
    "video": "Analyzing video",
    "credits": "Detecting credits",
    "complete": "Scan complete",
}
_SAFE_STATUS = {
    "queued": "Scan queued",
    "acquiring_model": "Acquiring scan model",
    "orphaned": "Scan interrupted",
    "awaiting_model_consent": "Awaiting model consent",
    "cancelling": "Cancelling scan",
    "cancelled": "Scan cancelled",
    "failed": "Scan could not complete",
    "error": "Scan could not complete",
    "complete": "Scan complete",
}
ScanT = TypeVar("ScanT", bound=Scan)


@dataclass(frozen=True)
class SeenScan:
    """One stored scan as this caller sees it."""

    facts: ScanJobFacts
    media: MediaDecision
    job: bool
    """The caller may view the job itself, not only its shared findings."""


def scan_not_found() -> Exception:
    from scanner import api as root

    return root._review_http_error("scan_not_found", "Scan not found", 404)


@dataclass
class ScanCaller:
    """The live principal behind one scan request, with its session and clock."""

    access: PrincipalAccess
    db: Session
    now: datetime

    @property
    def initiator(self) -> ActivityOwner:
        principal = self.access.principal
        return ActivityOwner(int(principal.user_id), int(principal.permission_revision))

    def allowed(self, action: Action, target) -> bool:
        return isinstance(decide(self.access, action, target), Allowed)

    def require(self, action: Action, target) -> None:
        decision = decide(self.access, action, target)
        if not isinstance(decision, Allowed):
            raise AccessDenied(decision.code)

    def sees_paths(self) -> bool:
        return C.MEDIA_PATHS_VIEW in self.access.capabilities

    def require_held(self, capability: C) -> None:
        """Refuse before reading a request's server path when the caller lacks `capability`.

        The media decision still follows once the path names a target.
        """

        if capability not in self.access.capabilities:
            raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)

    def decide_media(self, media: Sequence[ScanMedia]) -> list[MediaDecision]:
        return decide_scans(self.db, self.access.principal.account, media, self.now)

    def require_start(self, capability: C, media: ScanMedia) -> None:
        """Refuse a submission whose caller may not start this scan on this media."""

        self.require(capability, MediaRef(self.decide_media([media])[0]))

    @contextmanager
    def starting(self, capability: C, media: ScanMedia) -> Iterator[None]:
        """Gather provider evidence before locking, then recheck stored authority for a write."""
        principal = self.access.principal

        def state() -> JobState:
            return load_authority(
                self.db,
                principal.user_id,
                media,
                self.now,
                credential=principal.credential,
                session_id=self.access.session_id,
            )

        def prepare():
            before = state()
            if before.access is None:
                raise AccessDenied(AccessErrorCode.AUTHENTICATION_REQUIRED)
            account = before.access.principal.account
            evidence = gather_evidence(self.db, account, media, self.now)
            decision = decide_scans(self.db, account, [media], self.now, evidence=evidence)[0]
            self.access = before.access
            self.require(capability, MediaRef(decision))
            return before, evidence

        with fresh_authority_write(
            self.db,
            prepare,
            state,
            lambda: AccessDenied(AccessErrorCode.MEDIA_UNAVAILABLE),
            lock=write_lock,
        ) as evidence:
            decision = decide_scans(
                self.db, self.access.principal.account, [media], self.now, evidence=evidence
            )[0]
            self.require(capability, MediaRef(decision))
            yield

    def ref(self, seen: SeenScan) -> ResourceRef:
        facts = seen.facts
        owner = (
            AccountRef(UserId(facts.initiator_id), Role(facts.initiator_role))
            if facts.initiator_id is not None and facts.initiator_role is not None
            else None
        )
        return ResourceRef(owner, seen.media, existence_visible=True)

    def seen(self, scan_ids: Sequence[str]) -> dict[str, SeenScan]:
        """Every named scan this caller may see, keyed by ID."""

        facts = list(scan_job_facts(self.db, scan_ids).values())
        decisions = self.decide_media([item.media for item in facts])
        seen: dict[str, SeenScan] = {}
        for item, media in zip(facts, decisions, strict=True):
            candidate = SeenScan(item, media, job=False)
            job = self.allowed(JOB_VIEW, self.ref(candidate))
            shared = item.status == "complete" and self.allowed(
                C.SCANS_FINDINGS_READ, MediaRef(media, existence_visible=True)
            )
            if job or shared:
                seen[item.scan_id] = SeenScan(item, media, job)
        return seen

    def scan(self, scan_id: str) -> SeenScan:
        """One scan this caller may see, or `scan_not_found`."""

        found = self.seen([scan_id]).get(scan_id)
        if found is None:
            raise scan_not_found()
        return found

    def act(self, action: Action, scan_id: str) -> SeenScan:
        """A visible scan on which this caller may take `action`."""

        found = self.scan(scan_id)
        self.require(action, self.ref(found))
        return found

    def visible(self, scans: Sequence[ScanT], *, jobs_only: bool = False) -> list[ScanT]:
        """The scans this caller may see, in order, presented for this caller."""

        seen = self.seen([scan.id for scan in scans])
        shown = [scan for scan in scans if scan.id in seen and (seen[scan.id].job or not jobs_only)]
        jobs = self._job_keys() if any(isinstance(s, ScanQueueItem) for s in shown) else None
        return [self.present(scan, seen[scan.id], jobs=jobs) for scan in shown]

    def _job_keys(self) -> list[tuple[int, int]]:
        """The sorted queue keys of every job this caller may view."""

        queue = list_scan_queue(self.db)
        seen = self.seen([item.id for item in queue])
        return [
            (item.sort_order, item.queue_id)
            for item in queue
            if item.id in seen and seen[item.id].job
        ]

    def present(
        self,
        scan: ScanT,
        seen: SeenScan | None = None,
        *,
        jobs: list[tuple[int, int]] | None = None,
    ) -> ScanT:
        """Project an already-authorized scan response for this caller.

        A queue entry's `sort_order` becomes the number of jobs ahead of it that
        this caller may view, so hidden jobs cannot be counted. `jobs` reuses
        `_job_keys()` across one response.
        """

        if seen is None:
            facts = scan_job_facts(self.db, [scan.id])[scan.id]
            seen = SeenScan(facts, self.decide_media([facts.media])[0], job=True)
        update: dict[str, object] = {
            "is_own": seen.facts.initiator_id == int(self.access.principal.user_id)
        }
        if isinstance(scan, Scan) and scan.verified_from_scan_id:
            source = self.seen([scan.verified_from_scan_id]).get(scan.verified_from_scan_id)
            if source is None:
                update["verified_from_scan_id"] = None
            if source is None or not self.allowed(
                C.SCANS_FINDINGS_READ, MediaRef(source.media, existence_visible=True)
            ):
                update["verification_coverage"] = None
        if isinstance(scan, ScanQueueItem):
            keys = self._job_keys() if jobs is None else jobs
            update["sort_order"] = bisect_left(keys, (scan.sort_order, scan.queue_id))
        if isinstance(scan, ScanQueueItem) and scan.status == "acquiring_model":
            own = seen.facts.initiator_id == int(self.access.principal.user_id)
            progress = (
                self.allowed(C.MODELS_PROGRESS_VIEW_OWN, self.ref(seen))
                if own
                else self.allowed(C.SCAN_JOBS_VIEW_OTHER, self.ref(seen))
            )
            if not progress:
                update.update(progress_percent=None, progress_message=None)
        if not self.sees_paths():
            if scan.error_message:
                update["error_message"] = (
                    "Scan interrupted" if scan.status == "orphaned" else "Scan could not complete"
                )
            if isinstance(scan, ScanQueueItem):
                stage = scan.progress_stage
                update["progress_stage"] = stage if stage in _SAFE_PROGRESS else None
                if update.get("progress_message", scan.progress_message) is not None:
                    update["progress_message"] = _SAFE_STATUS.get(
                        scan.status, _SAFE_PROGRESS.get(stage, "Scan in progress")
                    )
        return scan.model_copy(update=update) if update else scan

    def require_reorder(self) -> None:
        self.require(C.SCAN_JOBS_REORDER, INSTANCE)


def scan_caller(
    access: PrincipalAccess = Depends(request_access), db: Session = Depends(get_db)
) -> ScanCaller:
    """FastAPI dependency for a scan handler that decides its own targets."""

    return ScanCaller(access, db, datetime.now(UTC))
