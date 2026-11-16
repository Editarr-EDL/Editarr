"""Confirmed Discord-assisted recovery with replacement-signed reconciliation."""

import base64
import hashlib
from dataclasses import replace
from urllib.parse import urlencode

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scanner.persistence.repository.repository_community import load_local_identity
from scanner.runtime.maintenance import DatabaseMaintenance
from scanner.security.community_key_recovery import (
    activate_recovery,
    bind_recovery,
    prepare_recovery,
)
from scanner.security.private_identity_files import IdentityUnavailable, locked, private_directory

from .transport import CommunityFailure


class CommunityRecovery:
    def __init__(self, client):
        self.client = client

    def _identity(self):
        if self.client.disconnected():
            raise CommunityFailure("not_connected")
        identity = load_local_identity(self.client.db, self.client.home, recovery=True)
        if identity.service_url != self.client.transport.endpoint.url:
            raise CommunityFailure("wrong_service")
        return identity

    def status(self):
        """Expose public recovery metadata only, without network, key creation or activation."""
        try:
            identity = self._identity()
        except IdentityUnavailable as error:
            if error.status == "incomplete":
                return {"configured": True, "identity": None, "pending": None, "completed": None}
            raise
        record = identity.recovery
        public = (
            None
            if not record
            else {
                "operationId": record["operationId"],
                "stateId": record["stateId"],
                "oldKeyId": record["oldKeyId"],
                "newKeyId": record["newKeyId"],
            }
        )
        return {
            "configured": True,
            "serviceUrl": self.client.transport.endpoint.url,
            "identity": {
                "installationId": identity.installation_id,
                "keyId": identity.key_id,
                "contributorId": identity.contributor_id,
            },
            "pending": public if record and record["state"] == "pending" else None,
            "completed": public if record and record["state"] == "completed" else None,
        }

    def start(self, old_key_id: str):
        """Persist one candidate before opening a separately labeled fresh OAuth recovery."""
        with DatabaseMaintenance(self.client.data_dir).worker_lease():
            with locked(
                private_directory(self.client.home), lock_name="rotation.lock", blocking=False
            ):
                self._identity()
                identity = prepare_recovery(
                    self.client.home, self.client.transport.endpoint.audience, old_key_id
                )
                record = identity.recovery
                parameters = {
                    "installationId": identity.installation_id,
                    "oldKeyId": record["oldKeyId"],
                    "operationId": record["operationId"],
                    "keyId": record["newKeyId"],
                    "publicKey": record["publicKey"],
                    "confirmationHash": hashlib.sha256(
                        ("editarr-enrollment-confirmation-v1:" + record["secret"]).encode()
                    ).hexdigest(),
                }
                return self.status() | {
                    "authorizationUrl": self.client.transport.endpoint.url
                    + "/v1/discord/recovery-start?"
                    + urlencode(parameters)
                }

    async def run(self, *, state_id=None, resend=False):
        """Probe before a retry; never change the candidate or assume an uncertain commit."""
        with DatabaseMaintenance(self.client.data_dir).worker_lease():
            with locked(
                private_directory(self.client.home), lock_name="rotation.lock", blocking=False
            ):
                identity = self._identity()
                if state_id is not None:
                    identity = bind_recovery(self.client.home, state_id)
                record = identity.recovery
                if not record or record["stateId"] is None:
                    raise CommunityFailure("recovery_not_pending")
                if record["state"] == "completed":
                    self.client._project(identity)
                    return self.status()
                candidate = replace(
                    identity,
                    key_id=record["newKeyId"],
                    public_key=base64.b64decode(record["publicKey"], validate=True),
                    private_key=Ed25519PrivateKey.from_private_bytes(
                        base64.b64decode(record["candidatePrivateKey"], validate=True)
                    ),
                )
                try:
                    response = await self.client.transport.request(
                        "POST",
                        "/v1/keys/recovery-status",
                        identity=candidate,
                        body=record["body"].encode("ascii"),
                        operation_id=record["operationId"],
                        candidate_proof=True,
                    )
                except CommunityFailure as error:
                    if error.code not in {
                        "recovery_expired",
                        "invalid_recovery",
                        "operation_conflict",
                    }:
                        raise
                    operation = await self.client.transport.request(
                        "GET",
                        "/v1/key-operations/key_recovery/" + record["operationId"],
                        identity=candidate,
                        candidate_proof=True,
                    )
                    response = {"version": 1, **operation}
                self._envelope(response)
                if response["state"] == "not_committed":
                    if response["result"] is not None:
                        raise CommunityFailure("invalid_response")
                    if not resend:
                        return self.status() | {"recoveryStatus": "not_committed"}
                    response = await self.client.transport.request(
                        "POST",
                        "/v1/keys/recover",
                        identity=candidate,
                        body=record["body"].encode("ascii"),
                        operation_id=record["operationId"],
                        candidate_proof=True,
                    )
                    self._envelope(response)
                if response["state"] != "completed":
                    raise CommunityFailure("invalid_response")
                updated = activate_recovery(self.client.home, response["result"])
                self.client._project(updated)
                return self.status()

    @staticmethod
    def _envelope(response):
        if (
            not isinstance(response, dict)
            or set(response) != {"version", "state", "result"}
            or type(response["version"]) is not int
            or response["version"] != 1
        ):
            raise CommunityFailure("invalid_response")
