"""Receipt-bound filter votes through the operation outbox, and reports (local API §6).

A vote is persisted as an exact intent before it is sent and is retried only with the
saved bytes. It uses the receipt of the link's latest snapshot, which must belong to
the voting contributor and contain the filter.
"""

import json
import re
import uuid

from sqlalchemy import select

from scanner.db import CommunityMasterFilterRow, CommunityOperationRow, CommunityPublicationRow
from scanner.formats.community.artifact_v3 import ArtifactV3Error, decode_artifact_v3
from scanner.persistence.repository.community_master_filters import set_filter_vote
from scanner.persistence.repository.community_master_links import latest_snapshot, require_link
from scanner.persistence.repository.repository_community import (
    enqueue_operation,
    set_operation_state,
)
from scanner.persistence.write_lock import write_lock

from .master_service import MasterService, encode, require_token
from .transport import CommunityFailure

VOTES = ("up", "down", "none")
REPORT_REASONS = ("spam", "inaccurate", "privacy", "rights", "other")
_OPERATION = re.compile(r"vote-[A-Za-z0-9._~-]{1,123}")
_OPEN = ("pending", "submitted", "failed")


def _ineligible(reason: str) -> CommunityFailure:
    return CommunityFailure("vote_ineligible", details={"reason": reason})


def snapshot_filter_ids(snapshot_bytes: bytes) -> set[str]:
    """Every filter ID a stored snapshot contains, including merged IDs."""
    document = json.loads(snapshot_bytes)
    return {
        identifier
        for item in document["filters"]
        for identifier in (item["filterId"], *item.get("mergedIds", ()))
    }


def _hosted_result(operation) -> dict | None:
    """Rebuild an acknowledged vote's hosted result from its stored intent and revision."""
    if operation.state != "acknowledged" or not (operation.revision_id or "").isdigit():
        return None
    return {
        "version": 1,
        "operationId": operation.operation_id,
        "filterId": operation.filter_id,
        "versionId": operation.community_version_id,
        "vote": operation.vote,
        "revision": int(operation.revision_id),
    }


class MasterFeedback:
    """Votes and reports for one account's identity."""

    def __init__(self, client, owner_id: int):
        self.client = client
        self.db = client.db
        self.owner_id = owner_id
        self.service = MasterService(client)

    def _receipt(self, link_id: str, filter_id: str | None, identity):
        """The link and its latest snapshot receipt, owned by `identity` and covering the
        filter. A link moved here by account transfer keeps the original contributor's
        receipts, so it is refused with `receipt_owner_mismatch`."""
        link = require_link(self.db, self.owner_id, link_id)
        snapshot = latest_snapshot(self.db, link)
        if snapshot is None:
            raise _ineligible("receipt_required")
        receipt = json.loads(snapshot.receipt_json)["receipt"]
        if (
            link.contributor_id != identity.contributor_id
            or receipt.get("contributorId") != identity.contributor_id
            or link.service_url != self.service.service_url
        ):
            raise CommunityFailure("receipt_owner_mismatch")
        if filter_id is not None and filter_id not in snapshot_filter_ids(snapshot.snapshot_bytes):
            raise _ineligible("receipt_required")
        return link, snapshot

    def _self_vote(self, identity, version_id: str, filter_id: str) -> bool:
        """Whether an acknowledged submission of this contributor proposed support for the
        filter. The service decides; this only avoids sending a vote it must refuse."""
        rows = self.db.execute(
            select(CommunityPublicationRow.artifact_bytes, CommunityPublicationRow.result_json)
            .join(
                CommunityOperationRow,
                CommunityOperationRow.operation_id == CommunityPublicationRow.operation_id,
            )
            .where(
                CommunityOperationRow.kind == "submission",
                CommunityOperationRow.state == "acknowledged",
                CommunityOperationRow.contributor_id == identity.contributor_id,
                CommunityOperationRow.service_url == self.service.service_url,
            )
        ).all()
        for artifact_bytes, result_json in rows:
            if not result_json or json.loads(result_json).get("versionId") != version_id:
                continue
            try:
                artifact = decode_artifact_v3(artifact_bytes).artifact
            except ArtifactV3Error:
                continue
            if any(
                entry.kind == "support" and entry.proposed_filter_id == filter_id
                for entry in artifact.entries
            ):
                return True
        return False

    def prepare_vote(self, link_id: str, filter_id: str, vote: str, operation_id: str) -> None:
        """Commit the exact vote intent; a different open vote on the filter is
        `vote_pending`."""
        if vote not in VOTES or not _OPERATION.fullmatch(operation_id):
            raise CommunityFailure("invalid_vote")
        require_token(filter_id, "invalid_vote")
        identity = self.service.identity()
        self.db.rollback()
        try:
            with write_lock(self.db):
                link, snapshot = self._receipt(link_id, filter_id, identity)
                row = self.db.get(CommunityMasterFilterRow, (link.id, filter_id))
                if row is None or not row.present:
                    raise CommunityFailure("filter_not_found")
                if vote != "none" and self._self_vote(
                    identity, link.community_version_id, filter_id
                ):
                    raise _ineligible("self_vote")
                pending = self.db.scalar(
                    select(CommunityOperationRow).where(
                        CommunityOperationRow.kind == "filter_vote",
                        CommunityOperationRow.contributor_id == identity.contributor_id,
                        CommunityOperationRow.filter_id == filter_id,
                        CommunityOperationRow.state.in_(_OPEN),
                        CommunityOperationRow.operation_id != operation_id,
                    )
                )
                if pending is not None and pending.state != "failed":
                    raise CommunityFailure("vote_pending")
                body = {"version": 1, "vote": vote, "receiptId": snapshot.receipt_id}
                enqueue_operation(
                    self.db,
                    credential_ref=self.client.home.credential_ref,
                    operation_id=operation_id,
                    service_url=self.service.service_url,
                    kind="filter_vote",
                    contributor_id=identity.contributor_id,
                    installation_id=identity.installation_id,
                    community_version_id=link.community_version_id,
                    filter_id=filter_id,
                    receipt_id=snapshot.receipt_id,
                    vote=vote,
                    receipt=encode(body).decode(),
                    actor_id=self.owner_id,
                )
                self.db.commit()
        except ValueError as error:
            self.db.rollback()
            raise CommunityFailure("invalid_vote_intent") from error

    async def send_vote(self, link_id: str, filter_id: str, operation_id: str) -> dict:
        """Send the saved vote bytes with a fresh signature and record the acknowledged
        vote. A retryable failure keeps the intent pending; a refusal marks it failed."""
        identity = self.service.identity()
        operation = self.db.get(CommunityOperationRow, operation_id, populate_existing=True)
        if operation is None or (
            operation.kind,
            operation.filter_id,
            operation.contributor_id,
            operation.service_url,
        ) != ("filter_vote", filter_id, identity.contributor_id, self.service.service_url):
            raise CommunityFailure("vote_intent_unavailable")
        link = require_link(self.db, self.owner_id, link_id)
        if link.community_version_id != operation.community_version_id:
            raise CommunityFailure("vote_intent_unavailable")
        body, vote, state = operation.receipt.encode(), operation.vote, operation.state
        stored = _hosted_result(operation)
        self.db.rollback()
        if state == "acknowledged":
            return {"version": 1, "state": "completed", "result": stored}
        self._set_state(operation_id, "submitted")
        try:
            result = await self.service.vote(filter_id, body, operation_id)
        except CommunityFailure as error:
            if not error.retryable:
                self._set_state(operation_id, "failed")
            raise
        if result.vote != vote:
            raise CommunityFailure("invalid_response")
        with write_lock(self.db):
            acknowledged = set_operation_state(
                self.db, operation_id=operation_id, state="acknowledged"
            )
            # The hosted result is the intent plus the revision it produced.
            acknowledged.revision_id = str(result.revision)
            set_filter_vote(self.db, self.owner_id, link_id, filter_id, vote)
            self.db.commit()
        return {
            "version": 1,
            "state": "completed",
            "result": result.model_dump(mode="json", by_alias=True),
        }

    def _set_state(self, operation_id: str, state: str) -> None:
        with write_lock(self.db):
            set_operation_state(self.db, operation_id=operation_id, state=state)
            self.db.commit()

    async def vote(self, link_id: str, filter_id: str, vote: str, operation_id: str) -> dict:
        """Persist the intent, then send it (local API §6 vote)."""
        operation = self.db.get(CommunityOperationRow, operation_id)
        self.db.rollback()
        if operation is None:
            self.prepare_vote(link_id, filter_id, vote, operation_id)
        elif operation.vote != vote:
            raise CommunityFailure("operation_conflict")
        return await self.send_vote(link_id, filter_id, operation_id)

    async def report(
        self,
        link_id: str,
        *,
        target_type: str,
        target_id: str,
        reason: str,
        note: str | None = None,
        operation_id: str | None = None,
    ) -> dict:
        """Proxy `POST /v1/reports` for the link's version or one of its served filters."""
        if (
            target_type not in ("filter", "version")
            or reason not in REPORT_REASONS
            or (note is not None and (not isinstance(note, str) or len(note) > 1000))
        ):
            raise CommunityFailure("invalid_report")
        require_token(target_id, "invalid_report")
        identity = self.service.identity()
        link, _snapshot = self._receipt(
            link_id, target_id if target_type == "filter" else None, identity
        )
        if target_type == "version" and target_id != link.community_version_id:
            self.db.rollback()
            raise CommunityFailure("report_ineligible")
        self.db.rollback()
        document = {"version": 1, "target": {"type": target_type, "id": target_id}}
        document["reason"] = reason
        if note is not None:
            document["note"] = note
        result = await self.service.report(document, operation_id or "report-" + uuid.uuid4().hex)
        return {
            "version": 1,
            "state": "completed",
            "result": result.model_dump(mode="json", by_alias=True),
        }
