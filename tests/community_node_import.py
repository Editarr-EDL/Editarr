"""Import actual Node HTTP download bytes into two disposable local installations."""

import base64
import json
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import (
    CommunityImportRow,
    CommunityOperationRow,
    FilterSelectionRuleRow,
    MediaItemRow,
)
from scanner.formats.community.artifact_v2 import validate_v2
from scanner.formats.community.codec import ResourceBounds
from scanner.integrations.community.downloads import verify_download_bundle
from scanner.models import ReviewCreate, ReviewScanMembership
from scanner.persistence.migrations.adoption import upgrade_existing_database
from scanner.persistence.repository.community_import_projection import LocalImportTarget
from scanner.persistence.repository.community_imports import import_verified_download
from scanner.persistence.repository.repository_community import set_connection
from scanner.security.community_download_receipts import parse_receipt_keys
from tests.community_identity_support import only_identity_ref
from tests.community_schema_fixture import create_unversioned_fixture


def exercise(value):
    """Require exact bytes, durable reuse and explicit review before acknowledging import."""
    bounds = ResourceBounds(65536, 524288, 2500, 4096, 30)
    bundle = value["bundle"]
    downloaded = verify_download_bundle(
        bundle,
        service="https://community.example",
        contribution_id=value["contributionId"],
        revision_id=bundle["revisionId"],
        contributor_id=value["contributorId"],
        trusted_keys=parse_receipt_keys(value["keys"]),
        bounds=bounds,
    )
    matching = validate_v2(downloaded.verified.artifact.artifact, source_limit=bounds.events)
    originals = tuple(
        base64.b64decode(bundle[name]) for name in ("artifact", "manifest", "envelope")
    )
    with tempfile.TemporaryDirectory(prefix="css66-import-") as directory:
        for installation in ("first", "restored-copy"):
            root = Path(directory) / installation
            root.mkdir()
            path = root / "scanner.db"
            create_unversioned_fixture(path).dispose()
            upgrade_existing_database(path, root)
            engine = create_engine(f"sqlite:///{path}")
            try:
                with Session(engine) as db:
                    db.query(
                        FilterSelectionRuleRow
                    ).one().id = "00000000-0000-0000-0000-000000000001"
                    set_connection(
                        db,
                        credential_ref=only_identity_ref(db),
                        service_url="https://community.example",
                        contributor_id=value["contributorId"],
                        installation_id=value["installationId"],
                        key_id=value["keyId"],
                    )
                    (root / "synthetic.mp4").write_bytes(b"synthetic media fixture")
                    db.get(MediaItemRow, "media-legacy").path = str(root / "synthetic.mp4")
                    db.commit()
                    target = LocalImportTarget(
                        "media-legacy", None, str(root / "synthetic.mp4"), matching
                    )
                    imported = import_verified_download(
                        db,
                        downloaded,
                        target,
                        bounds=bounds,
                        confirm_uncertain=True,
                        credential_ref=only_identity_ref(db),
                    )
                    assert not imported.reused and imported.receipt_state == "pending"
                    scan_id = imported.scan.id
                    assert imported.scan.community_provenance.author_id == "contributor-1"
                # Reopen the connection as a lost-response/restart reconciliation.
                with Session(engine) as db:
                    reused = import_verified_download(
                        db,
                        downloaded,
                        target,
                        bounds=bounds,
                        confirm_uncertain=True,
                        credential_ref=only_identity_ref(db),
                    )
                    assert reused.reused and reused.scan.id == scan_id
                    stored = db.query(CommunityImportRow).one()
                    assert (
                        stored.artifact_bytes,
                        stored.manifest_bytes,
                        stored.signature_bytes,
                    ) == originals
                    assert (
                        json.loads(db.query(CommunityOperationRow).one().receipt)
                        == bundle["receipt"]
                    )
                    review = repository.create_saved_review(
                        db,
                        media_item_id="media-legacy",
                        media_source_id=None,
                        duration_seconds=downloaded.verified.artifact.artifact.media.duration_seconds,
                        data=ReviewCreate(
                            name="Explicit imported review",
                            memberships=[ReviewScanMembership(scan_id=scan_id, role="audio")],
                        ),
                    )
                    assert len(review.filters) == len(
                        downloaded.verified.artifact.artifact.findings
                    )
                    assert (
                        repository.export_saved_review_edl(db, review.id, review.revision)
                        is not None
                    )
            finally:
                engine.dispose()
    print(json.dumps({"committed": True, "installations": 2}))


if __name__ == "__main__":
    exercise(json.load(sys.stdin))
