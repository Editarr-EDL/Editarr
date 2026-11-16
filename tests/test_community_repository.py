"""Local community operations do not require a service or network access."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from scanner.db import CommunityImportRow, CommunityOperationRow
from scanner.persistence.migrations.adoption import upgrade_existing_database
from scanner.persistence.repository.repository_community import (
    attach_import,
    enqueue_operation,
    find_import,
    load_local_identity,
    pending_after_restore,
    set_connection,
    set_operation_state,
)
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from scanner.security.private_identity_files import IdentityUnavailable
from tests.community_identity_support import identity_home, only_identity_ref
from tests.community_schema_fixture import create_unversioned_fixture

SERVICE = "https://community.example"
IMPORT = dict(
    scan_id="legacy-scan-001",
    service_url=SERVICE,
    contribution_id="contribution-1",
    revision_id="revision-1",
    digest="sha256:123",
    author_id="author-1",
    artifact_type="filter-set",
)
OPERATION = dict(
    operation_id="retry-1",
    service_url=SERVICE,
    kind="publish",
    contributor_id="author-1",
    installation_id="installation-1",
    scan_id="legacy-scan-001",
    digest="sha256:123",
    consent_version="consent-v1",
)


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "scanner.db"
    create_unversioned_fixture(path).dispose()
    upgrade_existing_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("UPDATE scans SET origin='imported' WHERE id='legacy-scan-001'"))
    yield engine
    engine.dispose()


def test_scan_and_provenance_rollback_and_idempotent_reuse(engine):
    with Session(engine) as db:
        with pytest.raises(RuntimeError, match="rollback"), db.begin():
            attach_import(db, **IMPORT)
            raise RuntimeError("rollback")
        assert (
            find_import(db, service_url=SERVICE, revision_id="revision-1", digest="sha256:123")
            is None
        )
        db.rollback()
        with db.begin():
            first = attach_import(db, **IMPORT)
            assert attach_import(db, **IMPORT).id == first.id
        assert db.query(CommunityImportRow).count() == 1
        with pytest.raises(ValueError, match="conflicting metadata"):
            attach_import(db, **(IMPORT | {"author_id": "another"}))


def test_operation_retry_and_restore_reconciliation(engine):
    with Session(engine) as db:
        with db.begin():
            connection = set_connection(
                db,
                credential_ref=only_identity_ref(db),
                service_url=SERVICE,
                contributor_id="author-1",
                installation_id="installation-1",
                key_id="sha256:key-1",
            )
            assert connection.sharing_enabled is False
            with pytest.raises(ValueError, match="active sharing consent"):
                enqueue_operation(db, **OPERATION, credential_ref=only_identity_ref(db))
            set_connection(
                db,
                credential_ref=only_identity_ref(db),
                service_url=SERVICE,
                contributor_id="author-1",
                installation_id="installation-1",
                key_id="sha256:key-1",
                sharing_enabled=True,
                consent_version="consent-v1",
                consented_at="2026-09-27T00:00:00Z",
            )
            row = enqueue_operation(db, **OPERATION, credential_ref=only_identity_ref(db))
            assert (
                enqueue_operation(
                    db, **OPERATION, credential_ref=only_identity_ref(db)
                ).operation_id
                == row.operation_id
            )
            receipt = enqueue_operation(
                db,
                operation_id="receipt-1",
                service_url=SERVICE,
                kind="receipt",
                contributor_id="author-1",
                installation_id="installation-1",
                contribution_id="contribution-1",
                revision_id="revision-1",
                digest="sha256:123",
                receipt="service-signed-receipt",
                credential_ref=only_identity_ref(db),
            )
            vote = enqueue_operation(
                db,
                operation_id="vote-1",
                service_url=SERVICE,
                kind="vote",
                contributor_id="author-1",
                installation_id="installation-1",
                contribution_id="contribution-1",
                revision_id="revision-1",
                digest="sha256:123",
                vote="up",
                credential_ref=only_identity_ref(db),
            )
            assert receipt.receipt == "service-signed-receipt"
            assert vote.vote == "up"
            with pytest.raises(ValueError, match="different intent"):
                enqueue_operation(
                    db,
                    **(OPERATION | {"digest": "sha256:changed"}),
                    credential_ref=only_identity_ref(db),
                )
            set_operation_state(db, operation_id="retry-1", state="submitted")
    with Session(engine) as restored:
        pending = pending_after_restore(
            restored,
            service_url=SERVICE,
            contributor_id="author-1",
            installation_id="installation-1",
            key_id="sha256:key-1",
            credential_ref=only_identity_ref(restored),
        )
        assert [(row.operation_id, row.state) for row in pending] == [
            ("receipt-1", "pending"),
            ("vote-1", "pending"),
            ("retry-1", "submitted"),
        ]
        assert not pending_after_restore(
            restored,
            service_url=SERVICE,
            contributor_id="author-1",
            installation_id="another-installation",
            key_id="sha256:key-1",
            credential_ref=only_identity_ref(restored),
        )
        restored.rollback()
        with restored.begin():
            set_connection(
                restored,
                credential_ref=only_identity_ref(restored),
                service_url=SERVICE,
                contributor_id="author-1",
                installation_id="installation-1",
                key_id="sha256:key-1",
            )
        assert [
            row.operation_id
            for row in pending_after_restore(
                restored,
                service_url=SERVICE,
                contributor_id="author-1",
                installation_id="installation-1",
                key_id="sha256:key-1",
                credential_ref=only_identity_ref(restored),
            )
        ] == ["receipt-1", "vote-1"]
        assert restored.query(CommunityOperationRow).count() == 3


def test_missing_identity_does_not_create_new_key_for_enrolled_database(engine, tmp_path):
    with Session(engine) as db:
        with db.begin():
            set_connection(
                db,
                credential_ref=only_identity_ref(db),
                service_url=SERVICE,
                contributor_id="author-1",
                installation_id="installation-1",
                key_id="sha256:key-1",
            )
        with pytest.raises(IdentityUnavailable) as failure:
            load_local_identity(db, identity_home(tmp_path))
        assert failure.value.status == "incomplete"
    assert not (identity_home(tmp_path).root / "identity.json").exists()


def test_identity_guard_rejects_mismatched_registration(engine, tmp_path):
    identity = load_or_create_installation_identity(identity_home(tmp_path))
    with Session(engine) as db:
        with db.begin():
            set_connection(
                db,
                credential_ref=only_identity_ref(db),
                service_url=SERVICE,
                contributor_id="author-1",
                installation_id=identity.installation_id,
                key_id=identity.key_id,
            )
        with pytest.raises(IdentityUnavailable) as failure:
            load_local_identity(db, identity_home(tmp_path))
        assert failure.value.status == "corrupt"
        save_identity_registration(
            identity_home(tmp_path),
            contributor_id="author-1",
            service_url=SERVICE,
            last_known_key_state="active",
        )
        assert load_local_identity(db, identity_home(tmp_path)).key_id == identity.key_id


def test_publication_exact_bytes_commit_rollback_and_reopen(engine):
    import hashlib

    from scanner.db import CommunityPublicationRow
    from scanner.persistence.repository.community_publications import stage_publication
    from scanner.security.community_artifact_signatures import SignedArtifact

    signed = SignedArtifact(b"gzip-original", b"manifest-original", b"signature-original")
    intent = OPERATION | {
        "digest": hashlib.sha256(signed.artifact_bytes).hexdigest(),
        "revision_id": "submission-1",
        "contribution_id": "root-1",
    }
    intent.pop("kind")
    values = dict(
        signed=signed,
        preview_digest="a" * 64,
        consent_token="b" * 64,
        author_key_id="sha256:key-1",
        **intent,
    )
    with Session(engine) as db:
        with db.begin():
            values["credential_ref"] = only_identity_ref(db)
            set_connection(
                db,
                credential_ref=values["credential_ref"],
                service_url=SERVICE,
                contributor_id="author-1",
                installation_id="installation-1",
                key_id="sha256:key-1",
                sharing_enabled=True,
                consent_version="consent-v1",
                consented_at="2026-10-03T00:00:00Z",
            )
        with pytest.raises(RuntimeError, match="crash"), db.begin():
            stage_publication(db, **values)
            raise RuntimeError("crash before commit")
        assert db.get(CommunityPublicationRow, "retry-1") is None
        assert db.get(CommunityOperationRow, "retry-1") is None
        db.rollback()
        with db.begin():
            stage_publication(db, **values)
    with Session(engine) as reopened:
        row = stage_publication(reopened, **values)
        assert (row.artifact_bytes, row.manifest_bytes, row.signature_bytes) == (
            signed.artifact_bytes,
            signed.manifest_bytes,
            signed.envelope_bytes,
        )
        with pytest.raises(ValueError, match="different approved bytes"):
            stage_publication(reopened, **(values | {"consent_token": "c" * 64}))
        assert reopened.query(CommunityPublicationRow).count() == 1
