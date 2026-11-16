"""Post-commit import acknowledgments with original, independently retryable receipts."""

import asyncio
import hashlib
import json

from scanner.db import CommunityImportRow, CommunityOperationRow, ScanRow
from scanner.persistence.repository.community_imports import queue_import_receipt
from scanner.persistence.repository.repository_community import (
    identity_connection,
    set_operation_state,
)
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.decisions import AccessDenied
from scanner.security.community_download_receipts import (
    parse_receipt_keys,
    verify_download_receipt,
)

from .client import CommunityClient
from .transport import CommunityFailure


class ReceiptOutbox:
    def __init__(self, client: CommunityClient):
        self.client = client
        self.db = client.db

    def _clean(self):
        if self.db.in_transaction():
            raise CommunityFailure("import_session_not_clean")

    def recover(self, *, limit: int = 100) -> list[str]:
        """Stage missing receipt intents from committed imports, without network activity."""
        from sqlalchemy import func, literal

        self._clean()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("A bounded recovery batch is required")
        try:
            identity = self.client._identity()
            receipt_id = literal("import-") + func.json_extract(
                CommunityImportRow.receipt_json, "$.receipt.receiptId"
            )
            candidates = (
                self.db.query(CommunityImportRow.id)
                .outerjoin(CommunityOperationRow, CommunityOperationRow.operation_id == receipt_id)
                .filter(
                    CommunityImportRow.service_url == self.client.transport.endpoint.url,
                    CommunityImportRow.receipt_json.is_not(None),
                    func.json_extract(CommunityImportRow.receipt_json, "$.receipt.contributorId")
                    == identity.contributor_id,
                    CommunityOperationRow.operation_id.is_(None),
                )
                .order_by(CommunityImportRow.id)
            )
            recovered = []
            after = ""
            while len(recovered) < limit:
                imports = candidates.filter(CommunityImportRow.id > after).limit(limit).all()
                if not imports:
                    break
                for (import_id,) in imports:
                    after = import_id

                    def stage():
                        return queue_import_receipt(
                            self.db,
                            import_id,
                            credential_ref=self.client.home.credential_ref,
                            actor_id=getattr(self.client, "actor_id", None),
                        )

                    try:
                        authorize = getattr(self.client, "authorize_import", None)
                        authority = authorize(import_id) if authorize is not None else None
                        row = authority.run(stage) if authority is not None else stage()
                    except AccessDenied as error:
                        self.db.rollback()
                        if error.code != AccessErrorCode.NOT_FOUND:
                            raise
                        continue
                    if row.contributor_id != identity.contributor_id:
                        raise CommunityFailure("wrong_contributor")
                    recovered.append(import_id)
                    if len(recovered) == limit:
                        break
            self.db.commit()
            return recovered
        except BaseException:
            self.db.rollback()
            raise

    async def run(self, import_id: str) -> dict:
        """Send only persisted successful imports; retain submitted state on uncertainty."""
        self._clean()
        operation_id = None
        try:
            authorize = getattr(self.client, "authorize_import", None)
            authority = authorize(import_id) if authorize is not None else None
            imported = self.db.get(CommunityImportRow, import_id)
            if imported is None or self.db.get(ScanRow, imported.scan_id) is None:
                raise CommunityFailure("import_evidence_unavailable")
            if not imported.artifact_bytes or hashlib.sha256(
                imported.artifact_bytes
            ).hexdigest() != imported.digest.removeprefix("sha256:"):
                raise CommunityFailure("import_evidence_invalid")
            identity = self.client._identity()
            if identity.pending_replacement is not None:
                raise CommunityFailure("rotation_pending")

            def stage():
                return queue_import_receipt(
                    self.db,
                    import_id,
                    credential_ref=self.client.home.credential_ref,
                    actor_id=getattr(self.client, "actor_id", None),
                )

            operation = authority.run(stage) if authority is not None else stage()
            operation_id = operation.operation_id
            if (operation.service_url, operation.contributor_id, operation.installation_id) != (
                self.client.transport.endpoint.url,
                identity.contributor_id,
                identity.installation_id,
            ):
                raise CommunityFailure("identity_conflict")
            saved = json.loads(operation.receipt)
            expected = {
                "version": 1,
                "operationId": operation_id,
                "revisionId": imported.revision_id,
                "artifactDigest": imported.digest.removeprefix("sha256:"),
                "receiptId": saved["receipt"]["receiptId"],
                "counted": imported.author_id != identity.contributor_id,
            }
            if operation.state == "acknowledged":
                self.db.rollback()
                return {"importId": import_id, "state": "acknowledged", "result": expected}
            self.db.commit()
            history = await self.client.transport.request(
                "GET", "/v1/receipt-keys", identity=identity, verify_response_digest=True
            )
            if (
                set(history) != {"version", "service", "keys"}
                or type(history["version"]) is not int
                or history["version"] != 1
                or history["service"] != self.client.transport.endpoint.url
            ):
                raise CommunityFailure("invalid_receipt_keys")
            verify_download_receipt(
                saved,
                service=self.client.transport.endpoint.url,
                contributor_id=identity.contributor_id,
                revision_id=expected["revisionId"],
                artifact_digest=expected["artifactDigest"],
                trusted_keys=parse_receipt_keys(history["keys"]),
            )
            body = json.dumps({"version": 1, "receipt": saved}, separators=(",", ":")).encode()
            if len(body) > 8192:
                raise CommunityFailure("receipt_limit")

            def submit():
                set_operation_state(self.db, operation_id=operation_id, state="submitted")

            authority.run(submit) if authority is not None else submit()
            self.db.commit()
            response = await self.client.transport.request(
                "POST",
                "/v1/import-receipts",
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
                or type(response["result"].get("counted")) is not bool
                or type(response["result"].get("version")) is not int
            ):
                raise CommunityFailure("invalid_response")

            set_operation_state(self.db, operation_id=operation_id, state="acknowledged")
            self.db.commit()
            return {"importId": import_id, "state": "acknowledged", "result": expected}
        except asyncio.CancelledError:
            self.db.rollback()
            raise
        except (ValueError, TypeError, KeyError) as error:
            self.db.rollback()
            raise CommunityFailure("import_evidence_invalid") from error
        except BaseException:
            self.db.rollback()
            raise


async def acknowledge_committed_import(
    client: CommunityClient, import_id: str, fallback: str
) -> str:
    """Receipt failure cannot roll back or hide an already successful import."""
    client.db.rollback()
    try:
        return (await ReceiptOutbox(client).run(import_id))["state"]
    except Exception:
        client.db.rollback()
        try:
            imported = client.db.get(CommunityImportRow, import_id)
            connection = identity_connection(
                client.db, client.home.credential_ref, client.transport.endpoint.url
            )
            operation = (
                client.db.query(CommunityOperationRow)
                .filter_by(
                    kind="receipt",
                    service_url=imported.service_url,
                    scan_id=imported.scan_id,
                    revision_id=imported.revision_id,
                    digest=imported.digest,
                    contributor_id=connection.contributor_id,
                )
                .one_or_none()
                if imported is not None and connection is not None and connection.contributor_id
                else None
            )
            return operation.state if operation else fallback
        except Exception:
            return fallback
        finally:
            client.db.rollback()
