"""MU-23 mock community service for several local identities at once.

`MockService` is an async `httpx.MockTransport` handler. It learns each
installation's public key and confirmation hash from the browser authorization
URL, checks the confirmation secret and every request signature against that
installation, verifies rotation proofs with production validation, and assigns
each installation its own contributor. `overlap=n` holds requests until `n` are
in flight, so a test proves two accounts' operations overlapped. `lose` names
paths whose responses are lost after the service commits them, until a test clears
it, and `reject` maps
paths to an HTTP status and remote error code. Nothing here
contacts a real community service.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
from scanner.persistence.repository.repository_community import user_identity_home
from scanner.security.community_rotation_proofs import validate_rotation_body

SERVICE = "https://community.test"
AUDIENCE = "community.test"


def _key_id(public: bytes) -> str:
    return "sha256:" + hashlib.sha256(public).hexdigest()


class MockService:
    def __init__(self, *, overlap: int = 0) -> None:
        self.keys: dict[str, bytes] = {}
        self.retired: list[tuple[str, bytes]] = []
        self.hashes: dict[str, str] = {}
        self.contributors: dict[str, str] = {}
        self.committed: dict[str, dict] = {}
        self.recoveries: dict[str, dict] = {}
        self.requests: list[tuple[str, str, str]] = []
        self.lose: set[str] = set()
        self.reject: dict[str, tuple[int, str]] = {}
        self.overlap = overlap
        self._arrived = 0
        self._together: asyncio.Event | None = None

    def authorize(self, authorization_url: str) -> str:
        """Record what the hosted browser flow would bind; return the installation."""
        query = parse_qs(urlsplit(authorization_url).query)
        installation = query["installationId"][0]
        self.keys[installation] = base64.b64decode(query["publicKey"][0])
        self.hashes[installation] = query["confirmationHash"][0]
        return installation

    def register(self, identity) -> None:
        """Know an identity that enrolled before this service started."""
        self.keys[identity.installation_id] = identity.public_key
        self.contributors[identity.installation_id] = identity.contributor_id

    def profile(self, installation: str) -> dict:
        contributor = self.contributors.setdefault(installation, "contributor-" + installation[:8])
        return {
            "contributorId": contributor,
            "shortId": contributor[:8],
            "installationId": installation,
            "keyId": _key_id(self.keys[installation]),
            "keyState": "active",
            "displayName": "Cedar Frost Grove",
            "profileRevision": 1,
            "policyVersion": "test-v1",
            "protocolVersion": 1,
            "allowedOperations": ["identity:read", "identity:rename"],
        }

    def _signer(self, request: httpx.Request) -> str:
        """The installation whose current or candidate key signed `request`."""
        parameters = request.headers["signature-input"].split("=", 1)[1]
        names = re.match(r"\(([^)]*)\)", parameters).group(1).replace('"', "").split()
        values = {
            "@method": request.method,
            "@scheme": "https",
            "@authority": request.url.host,
            "@path": request.url.path,
            "@query": "?" + request.url.query.decode() if request.url.query else "?",
            **{name: request.headers.get(name) for name in names if not name.startswith("@")},
        }
        base = (
            "\n".join(f'"{name}": {values[name]}' for name in names)
            + '\n"@signature-params": '
            + parameters
        ).encode("ascii")
        signature = base64.b64decode(request.headers["signature"].split(":")[1])
        key_id = re.search(r'keyid="([^"]+)"', parameters).group(1)
        for installation, public in [*self.keys.items(), *self.retired, *self._candidates()]:
            if _key_id(public) == key_id:
                Ed25519PublicKey.from_public_bytes(public).verify(signature, base)
                return installation
        raise AssertionError("request signed by an unknown key")

    def _candidates(self):
        for installation, outcome in self.recoveries.items():
            yield installation, outcome["publicKey"]

    async def _meet(self) -> None:
        if not self.overlap:
            return
        if self._together is None:
            self._together = asyncio.Event()
        self._arrived += 1
        if self._arrived >= self.overlap:
            self._together.set()
        await asyncio.wait_for(self._together.wait(), 5)

    def _replace_key(self, installation: str, public: bytes) -> None:
        if self.keys[installation] != public:
            self.retired.append((installation, self.keys[installation]))
            self.keys[installation] = public

    def _respond(self, path: str, request: httpx.Request, value: dict) -> httpx.Response:
        if path in self.lose:
            raise httpx.ReadError("lost after commit", request=request)
        return httpx.Response(200, json=value)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        installation = self._signer(request)
        body = json.loads(request.content) if request.content else {}
        self.requests.append((path, installation, request.content.decode()))
        await self._meet()
        if path in self.reject:
            status, error = self.reject[path]
            return httpx.Response(status, json={"error": {"code": error}})
        if path == "/v1/enrollments/complete":
            secret = body["confirmationSecret"]
            expected = hashlib.sha256(
                ("editarr-enrollment-confirmation-v1:" + secret).encode("ascii")
            ).hexdigest()
            assert body["installationId"] == installation
            assert self.hashes[installation] == expected, "secret of another enrollment"
            return self._respond(path, request, self.profile(installation))
        if path == "/v1/identity":
            return httpx.Response(200, json=self.profile(installation))
        if path in {"/v1/keys/rotate", "/v1/keys/rotation-status"}:
            operation = request.headers["idempotency-key"]
            if path.endswith("status") and operation not in self.committed:
                return httpx.Response(
                    200, json={"version": 1, "state": "not_committed", "result": None}
                )
            if operation not in self.committed:
                new_public = base64.b64decode(body["newPublicKey"])
                validate_rotation_body(
                    request.content.decode(),
                    AUDIENCE,
                    operation,
                    installation,
                    body["oldKeyId"],
                    self.keys[installation],
                    _key_id(new_public),
                    new_public,
                )
                self.committed[operation] = {
                    "version": 1,
                    "kind": "key_rotation",
                    "operationId": operation,
                    "contributorId": self.contributors[installation],
                    "installationId": installation,
                    "oldKeyId": body["oldKeyId"],
                    "newKeyId": _key_id(new_public),
                    "committedAt": 1700000030000,
                }
                self._replace_key(installation, new_public)
            outcome = {"version": 1, "state": "completed", "result": self.committed[operation]}
            return self._respond(path, request, outcome)
        if path in {"/v1/keys/recovery-status", "/v1/keys/recover"}:
            recovery = self.recoveries[installation]
            if path.endswith("status") and not recovery["committed"]:
                return httpx.Response(
                    200, json={"version": 1, "state": "not_committed", "result": None}
                )
            recovery["committed"] = True
            self._replace_key(installation, recovery["publicKey"])
            outcome = {"version": 1, "state": "completed", "result": recovery["result"]}
            return self._respond(path, request, outcome)
        raise AssertionError(f"unexpected request {request.method} {path}")

    def expect_recovery(self, identity, state_id: str) -> None:
        """Accept the pending recovery journal of `identity` once confirmed with `state_id`."""
        record = identity.recovery
        self.recoveries[identity.installation_id] = {
            "committed": False,
            "publicKey": base64.b64decode(record["publicKey"]),
            "result": {
                "version": 1,
                "kind": "key_recovery",
                "operationId": record["operationId"],
                "stateId": state_id,
                "contributorId": identity.contributor_id,
                "installationId": identity.installation_id,
                "oldKeyId": record["oldKeyId"],
                "newKeyId": record["newKeyId"],
                "committedAt": 1700000030000,
            },
        }


def transport(service: MockService) -> CommunityTransport:
    async def no_delay(_seconds):
        return None

    return CommunityTransport(
        ServiceEndpoint(SERVICE, AUDIENCE), adapter=httpx.MockTransport(service), delay=no_delay
    )


def user_client(db, data_dir, user_id: int, service: MockService, *, create: bool = False):
    """A client on `user_id`'s own home, as the identity routes build it."""
    home = user_identity_home(db, data_dir, user_id, create=create)
    return CommunityClient(db, home, transport(service))
