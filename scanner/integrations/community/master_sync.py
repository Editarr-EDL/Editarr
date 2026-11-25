"""The account's daily bulk master-set revision check (local API §8, contract §7.5).

A run is claimed at most once per 24 hours per account and service. Every active link
the account may still reach is sent under one `runId` in batches of 500. Changed
versions are recorded as pending downloads, then their newer snapshots are downloaded for
review until the service rate limits `GET …/master`; the rest are drained on later page
loads after `Retry-After` without a new run. Merged and unavailable versions mark their
links. Each outcome records one notification per link, a change once it is downloaded.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from scanner.persistence.repository.community_master_links import (
    mark_link_state,
    require_link,
    utcnow,
)
from scanner.persistence.repository.community_master_pending import (
    clear_pending_download,
    defer_downloads,
    downloads_ready,
    mark_pending_download,
    pending_downloads,
)
from scanner.persistence.repository.community_master_sync import (
    FAILED_RETRY,
    SyncStatus,
    begin_sync,
    finish_sync,
    links_to_sync,
    record_notification,
    sync_status,
)
from scanner.persistence.repository.community_master_updates import store_latest_snapshot
from scanner.persistence.write_lock import write_lock
from scanner.security.community_master_snapshots import VerifiedMaster

from .master_service import SYNC_BATCH, MasterService
from .masters import verified_snapshot
from .transport import CommunityFailure

# Snapshot download failures that mark a changed link instead of failing the run.
_GONE = {
    "version_merged": "merged",
    "version_unavailable": "unavailable",
    "version_not_found": "unavailable",
}


@dataclass(frozen=True)
class SyncOutcome:
    ran: bool
    status: SyncStatus
    skipped: tuple[str, ...] = ()


class MasterSync:
    """Run and report the daily sync for one account's identity."""

    def __init__(self, client, owner_id: int, *, clock=utcnow, visible=None):
        """`visible(link)` decides whether a link's local version is still within the
        account's media access; links it refuses are not sent to the service."""
        self.client = client
        self.visible = visible
        self.db = client.db
        self.owner_id = owner_id
        self.service = MasterService(client)
        self.clock = clock

    @property
    def service_url(self) -> str:
        return self.service.service_url

    def status(self) -> SyncStatus:
        result = sync_status(self.db, self.owner_id, self.service_url, now=self.clock())
        self.db.rollback()
        return result

    def _write(self, action):
        if self.db.in_transaction():
            self.db.rollback()
        try:
            with write_lock(self.db):
                result = action()
                self.db.commit()
                return result
        except BaseException:
            self.db.rollback()
            raise

    async def run(self) -> SyncOutcome:
        """Run the sync when due; otherwise report the status with `ran=False`."""
        identity = self.service.identity()
        now = self.clock()
        run_id = self._write(
            lambda: begin_sync(
                self.db,
                self.owner_id,
                self.service_url,
                contributor_id=identity.contributor_id,
                now=now,
            )
        )
        if run_id is None:
            skipped = await self.drain()
            return SyncOutcome(False, self.status(), tuple(skipped))
        try:
            skipped = await self._run(run_id)
        except CommunityFailure as error:
            code = error.code
            wait = error.details.get("retryAfter")
            self._write(
                lambda: finish_sync(
                    self.db,
                    self.owner_id,
                    self.service_url,
                    run_id,
                    error_code=code,
                    retry_after=timedelta(seconds=wait) if wait else None,
                    now=self.clock(),
                )
            )
            raise
        except BaseException:
            self._write(
                lambda: finish_sync(
                    self.db,
                    self.owner_id,
                    self.service_url,
                    run_id,
                    error_code="sync_failed",
                    now=self.clock(),
                )
            )
            raise
        self._write(
            lambda: finish_sync(self.db, self.owner_id, self.service_url, run_id, now=self.clock())
        )
        return SyncOutcome(True, self.status(), tuple(skipped))

    async def _run(self, run_id: str) -> list[str]:
        """Check every link's revision, record newer ones as pending, then download them."""
        links = links_to_sync(self.db, self.owner_id, self.service_url)
        if self.visible is not None:
            links = [link for link in links if self.visible(link)]
        by_version: dict[str, list[tuple[str, int]]] = defaultdict(list)
        unavailable = {link.id for link in links if link.link_state == "unavailable"}
        for link in links:
            by_version[link.community_version_id].append((link.id, link.latest_revision))
        self.db.rollback()
        items = sorted(
            (version, min(revision for _link, revision in entries))
            for version, entries in by_version.items()
        )
        for start in range(0, len(items), SYNC_BATCH):
            response = await self.service.sync_revisions(run_id, items[start : start + SYNC_BATCH])
            listed = set()
            for change in response.changed:
                listed.add(change.version_id)
                for link_id, revision in by_version[change.version_id]:
                    if change.status != "changed":
                        self._gone(link_id, change.status, change.merged_into, revision)
                        continue
                    if link_id in unavailable:
                        self._restore(link_id)
                    if change.revision > revision:
                        self._write(
                            lambda link_id=link_id, change=change: mark_pending_download(
                                self.db, self.owner_id, link_id, change.revision
                            )
                        )
            # An omitted version is served and unchanged, so an unavailable link is back.
            for version, _revision in items[start : start + SYNC_BATCH]:
                if version not in listed:
                    for link_id, _known in by_version[version]:
                        if link_id in unavailable:
                            self._restore(link_id)
        self._write(lambda: defer_downloads(self.db, self.owner_id, self.service_url, None))
        return await self._drain()

    async def drain(self) -> list[str]:
        """Download pending snapshots outside a run once any `Retry-After` has passed.

        Does nothing while a run is in progress, since the run drains them itself.
        """
        status = self.status()
        if status.state == "running" or not status.pending_downloads:
            return []
        if not downloads_ready(self.db, self.owner_id, self.service_url, self.clock()):
            self.db.rollback()
            return []
        self.db.rollback()
        return await self._drain()

    async def _drain(self) -> list[str]:
        """Download pending snapshots until the service defers them; return skipped links.

        A failed download (for example `rate_limited`) keeps the rest pending until the
        service's `Retry-After`, or one hour, has passed.
        """
        links = pending_downloads(self.db, self.owner_id, self.service_url)
        if self.visible is not None:
            links = [link for link in links if self.visible(link)]
        pending = [(link.id, link.community_version_id, link.latest_revision) for link in links]
        self.db.rollback()
        skipped: list[str] = []
        for link_id, version_id, revision in pending:
            try:
                if not await self._changed(link_id, version_id, revision):
                    skipped.append(link_id)
            except CommunityFailure as error:
                wait = error.details.get("retryAfter")
                until = self.clock() + (timedelta(seconds=wait) if wait else FAILED_RETRY)
                self._write(
                    lambda: defer_downloads(self.db, self.owner_id, self.service_url, until)
                )
                break
        return skipped

    def _restore(self, link_id: str) -> None:
        """Follow a version again once it is served; a merged link stays merged."""

        def restore():
            if require_link(self.db, self.owner_id, link_id).link_state == "unavailable":
                mark_link_state(self.db, self.owner_id, link_id, "active", now=self.clock())

        self._write(restore)

    async def _changed(self, link_id: str, version_id: str, revision: int) -> bool:
        """Download and store the newer snapshot for review; False when it was skipped.

        The `changed` notification is recorded once the snapshot is stored.
        """
        try:
            master = await self.service.master(version_id, known_revision=revision)
        except CommunityFailure as error:
            if error.code not in _GONE:
                if error.code == "snapshot_invalid":
                    self._write(lambda: clear_pending_download(self.db, self.owner_id, link_id))
                    return False
                raise
            self._gone(link_id, _GONE[error.code], error.details.get("mergedInto"), revision)
            return True
        if not isinstance(master, VerifiedMaster):
            self._write(lambda: clear_pending_download(self.db, self.owner_id, link_id))
            return True
        snapshot = verified_snapshot(master)

        def store():
            if store_latest_snapshot(self.db, self.owner_id, link_id, snapshot, now=self.clock()):
                record_notification(
                    self.db,
                    self.owner_id,
                    link_id,
                    "changed",
                    from_revision=revision,
                    to_revision=snapshot.revision,
                    now=self.clock(),
                )
            clear_pending_download(self.db, self.owner_id, link_id)

        self._write(store)
        return True

    def _gone(self, link_id: str, state: str, merged_into: str | None, revision: int) -> None:
        def mark():
            mark_link_state(
                self.db, self.owner_id, link_id, state, merged_into=merged_into, now=self.clock()
            )
            clear_pending_download(self.db, self.owner_id, link_id)
            record_notification(
                self.db,
                self.owner_id,
                link_id,
                state,
                from_revision=revision,
                now=self.clock(),
            )

        self._write(mark)
