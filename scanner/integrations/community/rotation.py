"""Explicit local rotation and replacement-signed reconciliation."""

from dataclasses import replace

from scanner.runtime.maintenance import DatabaseMaintenance
from scanner.security.community_key_rotation import activate_rotation, prepare_rotation
from scanner.security.private_identity_files import locked, private_directory

from .transport import CommunityFailure


class CommunityRotation:
    def __init__(self, client):
        self.client = client

    async def run(self, *, prepare: bool = False, resend: bool = False):
        """Serialize sends/probes across processes; never discard an uncertain candidate."""
        client = self.client
        with DatabaseMaintenance(client.data_dir).worker_lease():
            with locked(private_directory(client.home), lock_name="rotation.lock", blocking=False):
                identity = client._identity()
                if prepare:
                    if identity.pending_replacement is not None:
                        raise CommunityFailure("rotation_pending")
                    identity = prepare_rotation(client.home, client.transport.endpoint.audience)
                    return await self._send(identity)
                record = identity.rotation
                if record is None:
                    raise CommunityFailure("rotation_not_pending")
                if record["state"] == "completed":
                    return client.status()
                candidate = identity.pending_replacement
                replacement = replace(
                    identity,
                    key_id=candidate.key_id,
                    public_key=candidate.public_key,
                    private_key=candidate.private_key,
                    pending_replacement=None,
                )
                response = await client.transport.request(
                    "POST",
                    "/v1/keys/rotation-status",
                    identity=replacement,
                    body=record["body"].encode("ascii"),
                    operation_id=record["operationId"],
                )
                self._envelope(response)
                if response["state"] == "completed":
                    return self._accept(identity, response["result"])
                if response["state"] != "not_committed" or response["result"] is not None:
                    raise CommunityFailure("invalid_response")
                if resend:
                    return await self._send(identity)
                return client.status() | {"rotationStatus": "not_committed"}

    async def _send(self, identity):
        record = identity.rotation
        response = await self.client.transport.request(
            "POST",
            "/v1/keys/rotate",
            identity=identity,
            body=record["body"].encode("ascii"),
            operation_id=record["operationId"],
        )
        self._envelope(response)
        if response["state"] != "completed":
            raise CommunityFailure("invalid_response")
        return self._accept(identity, response["result"])

    @staticmethod
    def _envelope(response):
        if (
            set(response) != {"version", "state", "result"}
            or type(response["version"]) is not int
            or response["version"] != 1
        ):
            raise CommunityFailure("invalid_response")

    def _accept(self, identity, response):
        updated = activate_rotation(self.client.home, identity.rotation["operationId"], response)
        self.client._project(updated)
        return self.client.status()
