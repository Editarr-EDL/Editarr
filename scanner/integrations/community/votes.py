"""Persist exact vote intents separately from private, freshly read current votes."""

import json
import re

from sqlalchemy import text

from scanner.db import CommunityImportRow, CommunityOperationRow
from scanner.persistence.repository.repository_community import (
    enqueue_operation,
    set_operation_state,
)

from .transport import CommunityFailure

VOTES = {"like", "dislike", "neutral"}


class VoteOutbox:
    def __init__(self, client):
        self.client = client
        self.db = client.db

    def _identity(self, revision_id):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", revision_id):
            raise CommunityFailure("invalid_vote")
        identity = self.client._identity()
        if not identity.contributor_id:
            raise CommunityFailure("not_connected")
        if identity.pending_replacement is not None:
            raise CommunityFailure("rotation_pending")
        return identity

    def _pending(self, identity, revision_id):
        return (
            self.db.query(CommunityOperationRow)
            .filter_by(
                kind="vote",
                service_url=self.client.transport.endpoint.url,
                contributor_id=identity.contributor_id,
                installation_id=identity.installation_id,
                revision_id=revision_id,
            )
            .filter(CommunityOperationRow.state.in_(("pending", "submitted", "failed")))
            .order_by(CommunityOperationRow.updated_at, CommunityOperationRow.operation_id)
            .limit(1)
            .one_or_none()
        )

    @staticmethod
    def _intent(operation):
        if operation is None:
            return None
        if operation.vote not in VOTES or operation.receipt != json.dumps(
            {"version": 1, "vote": operation.vote}, separators=(",", ":")
        ):
            raise CommunityFailure("legacy_vote_intent")
        return {
            "operationId": operation.operation_id,
            "vote": operation.vote,
            "state": operation.state,
        }

    async def read(self, revision_id):
        """Never cache own votes; retain local pending intent when remote reads fail."""
        if self.db.in_transaction():
            raise CommunityFailure("vote_session_not_clean")
        authorize = getattr(self.client, "authorize_revision", None)
        if authorize is not None:
            authorize(revision_id)
        try:
            identity = self._identity(revision_id)
            pending = self._intent(self._pending(identity, revision_id))
            imported = (
                self.db.query(CommunityImportRow)
                .filter_by(service_url=self.client.transport.endpoint.url, revision_id=revision_id)
                .one_or_none()
            )
            reason = (
                "self_vote"
                if imported and imported.author_id == identity.contributor_id
                else "import_required"
            )
            self.db.rollback()
            result = {
                "version": 1,
                "revisionId": revision_id,
                "scope": {
                    "serviceUrl": self.client.transport.endpoint.url,
                    "contributorId": identity.contributor_id,
                },
                "current": None,
                "totals": None,
                "pending": pending,
                "error": None,
            }
            try:
                own = await self.client.transport.request(
                    "GET", f"/v1/contributions/{revision_id}/vote", identity=identity
                )
                totals = await self.client.transport.request(
                    "GET", f"/v1/contributions/{revision_id}/votes", identity=identity
                )
                if (
                    set(own) != {"version", "revisionId", "vote", "eligible"}
                    or type(own["version"]) is not int
                    or own["version"] != 1
                    or own["revisionId"] != revision_id
                    or not isinstance(own["vote"], str)
                    or own["vote"] not in VOTES
                    or type(own["eligible"]) is not bool
                    or set(totals) != {"version", "revisionId", "likes", "dislikes"}
                    or type(totals["version"]) is not int
                    or totals["version"] != 1
                    or totals["revisionId"] != revision_id
                    or any(
                        type(totals[key]) is not int or not 0 <= totals[key] <= 2**53 - 1
                        for key in ("likes", "dislikes")
                    )
                    or totals["likes"] + totals["dislikes"] > 2**53 - 3
                ):
                    raise CommunityFailure("invalid_response")
                result["current"] = {
                    "vote": own["vote"],
                    "eligible": own["eligible"],
                    "reason": None if own["eligible"] else reason,
                }
                result["totals"] = {"likes": totals["likes"], "dislikes": totals["dislikes"]}
            except CommunityFailure as error:
                result["error"] = {"code": error.code, "retryable": error.retryable}
            return result
        finally:
            self.db.rollback()

    def prepare(self, revision_id, vote, operation_id):
        """Commit a serialized intent before sending, using only acknowledged local imports."""
        if self.db.in_transaction():
            raise CommunityFailure("vote_session_not_clean")
        authorize = getattr(self.client, "authorize_revision", None)
        authority = authorize(revision_id) if authorize is not None else None

        def operation():
            return self._prepare(revision_id, vote, operation_id)

        return authority.run(operation) if authority is not None else operation()

    def _prepare(self, revision_id, vote, operation_id):
        if vote not in VOTES or not re.fullmatch(r"vote-[A-Za-z0-9_.-]{1,120}", operation_id):
            raise CommunityFailure("invalid_vote")
        try:
            identity = self._identity(revision_id)
            if getattr(self.client, "activity_access", None) is None:
                self.db.rollback()
            if not self.db.connection().connection.driver_connection.in_transaction:
                self.db.execute(text("BEGIN IMMEDIATE"))
            imported = (
                self.db.query(CommunityImportRow)
                .filter_by(service_url=self.client.transport.endpoint.url, revision_id=revision_id)
                .one_or_none()
            )
            if imported is None or not imported.receipt_json:
                raise CommunityFailure("import_required")
            if imported.author_id == identity.contributor_id:
                raise CommunityFailure("self_vote")
            acknowledgment = (
                self.db.query(CommunityOperationRow)
                .filter_by(
                    kind="receipt",
                    service_url=self.client.transport.endpoint.url,
                    contributor_id=identity.contributor_id,
                    scan_id=imported.scan_id,
                    revision_id=revision_id,
                    digest=imported.digest,
                    state="acknowledged",
                )
                .one_or_none()
            )
            if (
                acknowledgment is None
                or acknowledgment.state != "acknowledged"
                or acknowledgment.kind != "receipt"
                or acknowledgment.revision_id != revision_id
                or acknowledgment.digest != imported.digest
                or acknowledgment.scan_id != imported.scan_id
                or acknowledgment.contributor_id != identity.contributor_id
                or acknowledgment.service_url != self.client.transport.endpoint.url
            ):
                raise CommunityFailure("import_acknowledgment_required")
            pending = self._pending(identity, revision_id)
            if pending is not None and pending.operation_id != operation_id:
                raise CommunityFailure("vote_pending")
            enqueue_operation(
                self.db,
                credential_ref=self.client.home.credential_ref,
                operation_id=operation_id,
                service_url=self.client.transport.endpoint.url,
                kind="vote",
                contributor_id=identity.contributor_id,
                installation_id=identity.installation_id,
                contribution_id=imported.contribution_id,
                revision_id=revision_id,
                digest=imported.digest,
                scan_id=imported.scan_id,
                vote=vote,
                receipt=json.dumps({"version": 1, "vote": vote}, separators=(",", ":")),
            )
            self.db.commit()
        except (ValueError, KeyError, TypeError) as error:
            self.db.rollback()
            raise CommunityFailure("invalid_vote_intent") from error
        except BaseException:
            self.db.rollback()
            raise

    async def run(self, revision_id, operation_id):
        """Retry original bytes with fresh signatures; saved success is not current vote state."""
        if self.db.in_transaction():
            raise CommunityFailure("vote_session_not_clean")
        authorize = getattr(self.client, "authorize_revision", None)
        authority = authorize(revision_id) if authorize is not None else None
        try:
            identity = self._identity(revision_id)
            operation = self.db.get(CommunityOperationRow, operation_id)
            if operation is None or (
                operation.kind,
                operation.service_url,
                operation.contributor_id,
                operation.installation_id,
                operation.revision_id,
            ) != (
                "vote",
                self.client.transport.endpoint.url,
                identity.contributor_id,
                identity.installation_id,
                revision_id,
            ):
                raise CommunityFailure("vote_intent_unavailable")
            intent = self._intent(operation)
            body = operation.receipt.encode()
            expected = {
                "version": 1,
                "operationId": operation_id,
                "revisionId": revision_id,
                "vote": intent["vote"],
            }
            if operation.state == "acknowledged":
                self.db.rollback()
                return {"version": 1, "state": "completed", "result": expected}

            def submit():
                set_operation_state(self.db, operation_id=operation_id, state="submitted")

            authority.run(submit) if authority is not None else submit()
            self.db.commit()
            response = await self.client.transport.request(
                "POST",
                f"/v1/contributions/{revision_id}/vote",
                identity=identity,
                body=body,
                operation_id=operation_id,
            )
            if (
                set(response) != {"version", "state", "result"}
                or type(response["version"]) is not int
                or response["version"] != 1
                or response["state"] != "completed"
                or not isinstance(response["result"], dict)
                or response["result"] != expected
                or type(response["result"].get("version")) is not int
            ):
                raise CommunityFailure("invalid_response")

            set_operation_state(self.db, operation_id=operation_id, state="acknowledged")
            self.db.commit()
            return response
        finally:
            self.db.rollback()
