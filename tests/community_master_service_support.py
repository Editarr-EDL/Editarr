"""A connected community client over a migrated database and a fake master-set service.

The fake service answers signed requests per `(method, path)` and signs master
snapshots and receipts with the shared `community-v3` fixture key.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
from scanner.persistence.repository.repository_community import ensure_identity_ref
from scanner.security.community_identity import load_or_create_installation_identity
from scanner.security.community_master_snapshots import (
    MasterReceiptFields,
    SnapshotSignature,
    master_receipt_identity,
    master_receipt_signing_bytes,
    snapshot_signing_bytes,
)
from scanner.security.private_identity_files import IdentityHome
from tests.community_master_support import OWNER, SERVICE, master_database

FIXTURES = Path(__file__).parents[1] / "community-service/test/fixtures/community-v3"
MASTER_FIXTURE = json.loads((FIXTURES / "master-snapshot.json").read_text())
CONTRIBUTOR = MASTER_FIXTURE["request"]["contributorId"]
SERVICE_KEY = Ed25519PrivateKey.from_private_bytes(
    bytes.fromhex(MASTER_FIXTURE["servicePrivateKeySeedHex"])
)
SERVICE_KEY_ID = MASTER_FIXTURE["receiptKeys"][0]["keyId"]
ISSUED_AT = 1795553648


def respond(status: int, document: dict, **headers) -> httpx.Response:
    """A JSON response carrying the content digest the transport verifies."""
    body = json.dumps(document, separators=(",", ":")).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    return httpx.Response(
        status,
        content=body,
        headers={
            "content-type": "application/json",
            "content-digest": f"sha-256=:{digest}:",
            **headers,
        },
    )


def error(status: int, code: str, **extra) -> httpx.Response:
    headers = {"retry-after": str(extra.pop("retry_after"))} if "retry_after" in extra else {}
    return respond(status, {"error": {"code": code, **extra}}, **headers)


class FakeService:
    """Route signed requests to handlers keyed by method and path (query excluded)."""

    def __init__(self):
        self.routes: dict[tuple[str, str], object] = {}
        self.requests: list[httpx.Request] = []
        self.on("GET", "/v1/receipt-keys", self.receipt_keys)

    def on(self, method: str, path: str, handler) -> None:
        self.routes[(method, path)] = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return error(404, "not_found")
        result = handler(request)
        return result if isinstance(result, httpx.Response) else respond(200, result)

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if (r.method, r.url.path) == (method, path)]

    @staticmethod
    def receipt_keys(_request) -> dict:
        return {"version": 1, "service": SERVICE, "keys": MASTER_FIXTURE["receiptKeys"]}


def connect(tmp_path, service: FakeService, *, owner: int = OWNER):
    """Return `(factory, client)`: a migrated database and a client enrolled as CONTRIBUTOR."""

    async def no_delay(_seconds):
        return None

    _engine, factory = master_database(tmp_path)
    db = factory()
    home = IdentityHome(tmp_path, ensure_identity_ref(db, owner))
    identity = load_or_create_installation_identity(home)

    def profile(_request):
        return {
            "contributorId": CONTRIBUTOR,
            "shortId": CONTRIBUTOR[:8],
            "installationId": identity.installation_id,
            "keyId": identity.key_id,
            "keyState": "active",
            "displayName": "Cedar Frost Grove",
            "profileRevision": 1,
            "policyVersion": "test-v1",
            "protocolVersion": 1,
            "allowedOperations": ["identity:read"],
        }

    service.on("POST", "/v1/enrollments/complete", profile)
    service.on("GET", "/v1/identity", profile)
    transport = CommunityTransport(
        ServiceEndpoint(SERVICE, SERVICE.removeprefix("https://")),
        adapter=httpx.MockTransport(service),
        delay=no_delay,
    )
    client = CommunityClient(db, home, transport)
    client.start()
    asyncio.run(client.complete("b" * 64))
    service.requests.clear()
    return factory, client


def served_filter(filter_id: str, start: int = 10_000, end: int = 20_000, **changes) -> dict:
    document = {
        "filterId": filter_id,
        "mergedIds": [],
        "category": "violence",
        "subcategory": "punching",
        "severity": "high",
        "action": "skip",
        "startMs": start,
        "endMs": end,
        "spread": {"startMs": 0, "endMs": 0},
        "confidence": 0.5,
        "contributorCount": 2,
        "support": {"attestations": 2, "upvotes": 0},
        "negative": {"downvotes": 0, "disputes": 0, "coverageNegatives": 0},
        "lowConfidence": False,
    }
    return {**document, **changes}


def master_response(
    version_id: str,
    revision: int,
    *filters: dict,
    contributor: str = CONTRIBUTOR,
    status: str = "confirmed",
) -> dict:
    """A signed `changed: true` master response for the given served filters."""
    snapshot = {
        "schemaVersion": "community-master-snapshot-1",
        "service": SERVICE,
        "versionId": version_id,
        "revision": revision,
        "generatedAt": ISSUED_AT,
        "work": {
            "workId": "w_77",
            "kind": "movie",
            "tmdb": "603",
            "tvdb": None,
            "season": None,
            "episode": None,
            "lastEpisode": None,
            "title": "The Matrix",
            "year": 1999,
        },
        "versionInfo": {
            "status": status,
            "aliasId": "theatrical",
            "label": "Theatrical",
            "labelText": None,
            "runtimeMs": 8_160_500,
            "trust": 0.41,
        },
        "filters": sorted(filters, key=lambda item: (item["startMs"], item["endMs"])),
    }
    raw = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(raw).hexdigest()
    signature = {
        "schemaVersion": "community-master-signature-1",
        "service": SERVICE,
        "serviceKeyId": SERVICE_KEY_ID,
        "versionId": version_id,
        "revision": revision,
        "snapshotDigest": digest,
        "issuedAt": ISSUED_AT,
    }
    signed = sign_snapshot(signature)
    receipt = {
        "schemaVersion": "community-master-receipt-1",
        "service": SERVICE,
        "serviceKeyId": SERVICE_KEY_ID,
        "contributorId": contributor,
        "versionId": version_id,
        "revision": revision,
        "snapshotDigest": digest,
        "receiptId": master_receipt_identity(SERVICE, contributor, version_id, revision, digest),
        "issuedAt": ISSUED_AT,
    }
    return {
        "version": 1,
        "changed": True,
        "snapshot": base64.b64encode(raw).decode(),
        "snapshotSignature": {**signature, "signature": signed},
        "receipt": {"receipt": receipt, "signature": sign_receipt(receipt)},
    }


def _sign(message: bytes) -> str:
    return base64.b64encode(SERVICE_KEY.sign(message)).decode()


def sign_snapshot(fields: dict) -> str:
    unsigned = SnapshotSignature.model_validate({**fields, "signature": ""})
    return _sign(snapshot_signing_bytes(unsigned))


def sign_receipt(fields: dict) -> str:
    return _sign(master_receipt_signing_bytes(MasterReceiptFields.model_validate(fields)))


def master_route(service: FakeService, version_id: str, responses: dict[int | None, dict]):
    """Serve `responses[knownRevision]` (None when absent) for one version's master."""

    def handle(request):
        known = request.url.params.get("knownRevision")
        return responses[int(known) if known else None]

    service.on("GET", f"/v1/versions/{version_id}/master", handle)


def found_download(masters, version_id, *, tier="2", local=None, replace=False):
    """Remember a Find result for `version_id` at `tier`, then download it as a route would."""
    import asyncio

    from scanner.integrations.community.find import FoundVersion
    from scanner.integrations.community.master_service_models import MatchResult
    from tests.community_master_support import VERSION_ID

    percent = {"1": 100, "2": 80, "3": 65, "4": 30, "5": 15}[tier]
    result = MatchResult.model_validate(
        {
            "versionId": version_id,
            "status": "confirmed",
            "aliasId": "theatrical",
            "label": "Theatrical",
            "labelText": None,
            "work": {
                "workId": "w_77",
                "kind": "movie",
                "tmdb": "603",
                "tvdb": None,
                "season": None,
                "episode": None,
                "lastEpisode": None,
                "title": "The Matrix",
                "year": 1999,
            },
            "tier": tier,
            "matchPercent": percent,
            "matchedKeys": ["md5_quick"] if tier in "123" else ["tmdb"],
            "runtimeAgreement": "not_applicable" if tier in "123" else "same_cut",
            "runtimeMs": 8160500,
            "trust": 0.41,
            "revision": 1,
            "filterCount": 1,
            "contributorCount": 1,
        },
        strict=True,
    )
    key = masters.remember(local or VERSION_ID, "guard", (FoundVersion(result),))
    return asyncio.run(
        masters.download(find_key=key, community_version_id=version_id, replace=replace)
    )
