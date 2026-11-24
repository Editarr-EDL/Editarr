"""Download, verify and apply master sets, and check one link for updates (local API §3, §8).

Every network call happens outside a database transaction; verified results are then
stored in one transaction. A merged or unavailable version marks the link and raises.
"""

import json

from scanner.db import CommunityMasterLinkRow
from scanner.formats.community.master_snapshot import MasterSnapshot
from scanner.modeling.community_master_diff import master_filters
from scanner.persistence.repository.community_master_links import (
    LinkVersion,
    VerifiedSnapshot,
    apply_master,
    live_link,
    mark_link_state,
    require_link,
)
from scanner.persistence.repository.community_master_updates import (
    pending_counts,
    store_latest_snapshot,
)
from scanner.persistence.write_lock import write_lock
from scanner.security.community_master_snapshots import MasterUnchanged, VerifiedMaster

from .find import alignment_required, find_versions, key_type, query_keys
from .find_store import FindStore, FoundMatch, finds
from .find_targets import CapturedFind
from .master_service import MasterService, require_token
from .transport import CommunityFailure

GONE = {"version_unavailable": "unavailable", "version_not_found": "unavailable"}


def verified_snapshot(master: VerifiedMaster) -> VerifiedSnapshot:
    """The persistence form of a verified master response, keeping the exact bytes."""
    signed = master.snapshot_signature
    return VerifiedSnapshot(
        revision=signed.revision,
        snapshot_digest=signed.snapshot_digest,
        snapshot_bytes=master.snapshot_bytes,
        signature=signed.model_dump(by_alias=True),
        service_key_id=signed.service_key_id,
        receipt=master.receipt.model_dump(by_alias=True),
        receipt_id=master.receipt.receipt.receipt_id,
        filters=master_filters(json.loads(master.snapshot_bytes)),
    )


def link_version(
    snapshot: MasterSnapshot, *, tier: str | None, match_percent: int | None
) -> LinkVersion:
    """The link's version description: signed snapshot facts plus the Find match."""
    info = snapshot.version_info
    return LinkVersion(
        community_version_id=snapshot.version_id,
        version_status=info.status,
        label=info.label,
        alias_id=info.alias_id,
        community_work_id=snapshot.work.work_id,
        tier=tier,
        match_percent=match_percent,
    )


class MasterSets:
    """Master-set download and update checks for one account's identity."""

    def __init__(self, client, owner_id: int, *, store: FindStore = finds):
        self.client = client
        self.store = store
        self.db = client.db
        self.owner_id = owner_id
        self.service = MasterService(client)

    def _clean(self):
        if self.db.in_transaction():
            self.db.rollback()

    def _mark_gone(self, link_id: str, error: CommunityFailure) -> None:
        state = "merged" if error.code == "version_merged" else GONE.get(error.code)
        if state is None:
            return
        self._clean()
        with write_lock(self.db):
            mark_link_state(
                self.db,
                self.owner_id,
                link_id,
                state,
                merged_into=error.details.get("mergedInto"),
            )
            self.db.commit()

    async def find(
        self,
        captured: CapturedFind,
        local_version_id: str,
        *,
        label: dict | None = None,
        limit: int = 20,
    ) -> dict:
        """Find versions for a captured target and remember them under a `findKey`.

        Returns the local API Find response (§2).
        """
        found = await find_versions(self.service, captured.keys, label=label, limit=limit)
        key = self.remember(local_version_id, captured.guard, found)
        return {
            "version": 1,
            "findKey": key,
            "localVersionId": local_version_id,
            "keysUsed": [key_type(key) for key in query_keys(captured.keys)],
            "results": [
                item.as_json(self.linked_link_id(local_version_id, item.result.version_id))
                for item in found
            ],
            "error": None,
        }

    def remember(self, local_version_id: str, guard: str, found) -> str:
        """Store Find results for this account; see `find_store`."""
        return self.store.put(
            owner_id=self.owner_id,
            service_url=self.service.service_url,
            local_version_id=local_version_id,
            guard=guard,
            matches={
                item.result.version_id: FoundMatch(item.result.tier, item.result.match_percent)
                for item in found
            },
        )

    async def download(
        self,
        *,
        find_key: str,
        community_version_id: str,
        replace: bool = False,
        guard: str | None = None,
    ) -> CommunityMasterLinkRow:
        """Fetch, verify and apply a found version's master set to the Find's local version.

        The tier and Match % come from the stored Find (`find_expired`, `target_changed`
        when `guard` names another file generation). Tiers 1–3 apply at once; other
        tiers stay inactive until the user approves an alignment. Another version
        already linked to the local version needs `replace=True` (`replace_required`).
        A merged version raises `version_merged` with `details["mergedInto"]`.
        """
        require_token(community_version_id, "invalid_master_request")
        record = self.store.get(
            find_key, owner_id=self.owner_id, service_url=self.service.service_url, guard=guard
        )
        match = record.matches.get(community_version_id)
        if match is None:
            raise CommunityFailure("find_expired")
        self._clean()
        master = await self.service.master(community_version_id)
        if not isinstance(master, VerifiedMaster):
            raise CommunityFailure("invalid_response")
        identity = self.service.identity()
        self.db.rollback()
        try:
            with write_lock(self.db):
                link = apply_master(
                    self.db,
                    owner_id=self.owner_id,
                    media_version_id=record.local_version_id,
                    service_url=self.service.service_url,
                    contributor_id=identity.contributor_id,
                    version=link_version(
                        master.snapshot, tier=match.tier, match_percent=match.match_percent
                    ),
                    snapshot=verified_snapshot(master),
                    alignment_required=alignment_required(match.tier),
                    replace=replace,
                )
                self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return link

    async def check(self, link_id: str) -> dict:
        """The item page's on-demand check: store a newer revision for review, never apply.

        It does not use the daily sync allowance.
        """
        self._clean()
        link = require_link(self.db, self.owner_id, link_id)
        version_id, known = link.community_version_id, link.latest_revision
        self.db.rollback()
        try:
            master = await self.service.master(version_id, known_revision=known)
        except CommunityFailure as error:
            self._mark_gone(link_id, error)
            raise
        if not isinstance(master, (VerifiedMaster, MasterUnchanged)):
            raise CommunityFailure("invalid_response")
        changed = False
        self._clean()
        try:
            with write_lock(self.db):
                if isinstance(master, VerifiedMaster):
                    changed = store_latest_snapshot(
                        self.db, self.owner_id, link_id, verified_snapshot(master)
                    )
                if require_link(self.db, self.owner_id, link_id).link_state == "unavailable":
                    # A restored version is served again, so the link follows it again.
                    mark_link_state(self.db, self.owner_id, link_id, "active")
                self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        link = require_link(self.db, self.owner_id, link_id)
        result = {
            "version": 1,
            "changed": changed,
            "latestRevision": link.latest_revision,
            "pendingUpdate": pending_counts(self.db, link.id),
        }
        self.db.rollback()
        return result

    def linked_link_id(self, media_version_id: str, community_version_id: str) -> str | None:
        """The account's live link for this local and community version, if any (Find)."""
        link = live_link(self.db, self.owner_id, media_version_id, self.service.service_url)
        found = link.id if link and link.community_version_id == community_version_id else None
        self.db.rollback()
        return found
