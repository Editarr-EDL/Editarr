"""Receipt recovery progresses through media-scoped candidate windows."""

import base64
import gzip
import hashlib
import itertools
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scanner.db import CommunityOperationRow
from scanner.formats.community.codec import parse_artifact
from scanner.http.community_activity_access import receipt_client
from scanner.integrations.community.receipts import ReceiptOutbox
from scanner.persistence.repository import repository_community
from scanner.security.access.authorization import require_principal
from scanner.security.access.identity import CredentialKind
from scanner.security.community_artifact_signatures import sign_artifact
from scanner.security.community_download_receipts import (
    ReceiptFields,
    receipt_identity,
    receipt_signing_bytes,
)
from tests import test_community_activity_feedback as feedback_module
from tests.multi_user_access_support import ADMIN, USER
from tests.scan_access_http import LIBRARY_A, LIBRARY_B
from tests.test_community_activity_access import activities as activities
from tests.test_community_activity_feedback import (
    feedback as feedback,
)
from tests.test_community_activity_feedback import (
    feedback_service as feedback_service,
)
from tests.test_community_activity_feedback import (
    import_for,
)
from tests.test_community_activity_http import community as community
from tests.test_community_verified_import import BOUNDS, RECEIPT_VECTOR
from tests.test_stream_fingerprints import generated_streams as generated_streams


@pytest.mark.parametrize("caller", [USER, ADMIN])
def test_recovery_limit_counts_authorized_imports(feedback, activities, caller):
    app, service, homes, scan, _review = feedback
    other_scan, _ = activities.seed_scan(
        USER, "other-library", library=LIBRARY_B, status="complete"
    )
    first = import_for(app, service, homes, scan, USER)[0]
    second = import_for(app, service, homes, other_scan, USER, "manual")[0]
    hidden, visible = sorted([first, second])
    activities.limit_to_library(caller, LIBRARY_B if visible == second else LIBRARY_A)
    with app.accounts.factory() as db:
        db.query(CommunityOperationRow).filter_by(kind="receipt").delete()
        db.commit()
    with app.accounts.factory() as db:
        access = require_principal(db, caller, CredentialKind.API_KEY)
        client = receipt_client(db, access, USER)
        assert ReceiptOutbox(client).recover(limit=1) == [visible]
    with app.accounts.factory() as db:
        rows = db.query(CommunityOperationRow).filter_by(kind="receipt").all()
        assert len(rows) == 1 and rows[0].owner_id == USER and rows[0].state == "pending"
    query = f"?user_id={USER}" if caller == ADMIN else ""
    result = app.send(
        "POST", f"/api/community/import-receipts/{hidden}/retry{query}", caller, {"confirmed": True}
    )
    assert result.status_code == 404 and result.json()["code"] == "not_found"
    assert service.feedback == []


def unique_bundles(original):
    """Produce distinct verified revisions and stable receipts for a full recovery window."""
    sequence = itertools.count(1000)
    signer = Ed25519PrivateKey.from_private_bytes(b"\x21" * 32)
    public = signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_id = "sha256:" + hashlib.sha256(public).hexdigest()
    receipt_signer = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(RECEIPT_VECTOR["syntheticSeedHex"])
    )

    def bundle(contributor, kind="manual"):
        value, keys = original(contributor, kind)
        payload = json.loads(gzip.decompress(base64.b64decode(value["artifact"])))
        payload["submission_id"] = str(UUID(int=next(sequence)))
        payload["author"]["key_id"] = key_id
        artifact = gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0)
        decoded = parse_artifact(artifact, bounds=BOUNDS, compressed=True)
        manifest = json.loads(base64.b64decode(value["manifest"]))
        manifest.update(
            submission_id=payload["submission_id"],
            key_id=key_id,
            artifact_digest=hashlib.sha256(artifact).hexdigest(),
            summary=decoded.summary,
        )
        signed = sign_artifact(
            private_key=signer,
            artifact_bytes=artifact,
            manifest_bytes=json.dumps(manifest, separators=(",", ":")).encode(),
            bounds=BOUNDS,
        )
        value.update(
            revisionId=payload["submission_id"],
            artifactDigest=manifest["artifact_digest"],
            manifestDigest=hashlib.sha256(signed.manifest_bytes).hexdigest(),
            envelopeDigest=hashlib.sha256(signed.envelope_bytes).hexdigest(),
            artifact=base64.b64encode(artifact).decode(),
            manifest=base64.b64encode(signed.manifest_bytes).decode(),
            envelope=base64.b64encode(signed.envelope_bytes).decode(),
        )
        value["historicalKey"].update(keyId=key_id, publicKey=base64.b64encode(public).decode())
        fields = value["receipt"]["receipt"]
        fields.update(
            revisionId=value["revisionId"],
            artifactDigest=value["artifactDigest"],
            receiptId=receipt_identity(
                fields["service"], contributor, value["revisionId"], value["artifactDigest"]
            ),
        )
        value["receipt"]["signature"] = base64.b64encode(
            receipt_signer.sign(receipt_signing_bytes(ReceiptFields.model_validate(fields)))
        ).decode()
        return value, keys

    return bundle


@pytest.mark.parametrize("caller", [USER, ADMIN])
def test_http_recovery_passes_more_than_one_hundred_hidden_candidates(
    feedback, activities, monkeypatch, caller
):
    app, service, homes, scan, _review = feedback
    hidden_scan, _ = activities.seed_scan(
        USER, "hidden-window", library=LIBRARY_B, status="complete"
    )
    identifiers = itertools.count(1)
    monkeypatch.setattr(
        repository_community, "uuid", SimpleNamespace(uuid4=lambda: UUID(int=next(identifiers)))
    )
    monkeypatch.setattr(feedback_module, "bundle", unique_bundles(feedback_module.bundle))
    hidden = [
        import_for(app, service, homes, hidden_scan, USER, "manual")[0] for _index in range(101)
    ]
    visible = import_for(app, service, homes, scan, USER, "manual")[0]
    assert max(hidden) < visible
    with app.accounts.factory() as db:
        db.query(CommunityOperationRow).filter_by(kind="receipt").delete()
        db.commit()
    activities.limit_to_library(caller, LIBRARY_A)
    query = f"?user_id={USER}" if caller == ADMIN else ""
    path = f"/api/community/import-receipts/recover{query}"
    result = app.send("POST", path, caller)
    assert result.status_code == 200 and result.json() == {"importIds": [visible]}, result.text
    repeat = app.send("POST", path, caller)
    assert repeat.status_code == 200 and repeat.json() == {"importIds": []}, repeat.text
    with app.accounts.factory() as db:
        rows = db.query(CommunityOperationRow).filter_by(kind="receipt").all()
        assert len(rows) == 1 and rows[0].owner_id == USER and rows[0].state == "pending"
    assert service.feedback == []
    refused = app.send(
        "POST",
        f"/api/community/import-receipts/{hidden[0]}/retry{query}",
        caller,
        {"confirmed": True},
    )
    assert refused.status_code == 404 and refused.json()["code"] == "not_found"
    accepted = app.send(
        "POST",
        f"/api/community/import-receipts/{visible}/retry{query}",
        caller,
        {"confirmed": True},
    )
    assert accepted.status_code == 200 and accepted.json()["state"] == "acknowledged", accepted.text
    assert len(service.feedback) == 1
