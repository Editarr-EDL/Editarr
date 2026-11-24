"""Transaction-scoped community provenance and durable local retry state."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityConnectionRow,
    CommunityIdentityRow,
    CommunityImportRow,
    CommunityOperationRow,
    SavedReviewRow,
    ScanRow,
    UserRow,
)
from scanner.security.community_identity import load_or_create_installation_identity
from scanner.security.community_identity_binding import projects_identity
from scanner.security.community_identity_homes import adopt_incoming_identity
from scanner.security.private_identity_files import IdentityHome, IdentityUnavailable

from .activity_ownership import activity_owner


def identity_ref(db: Session, user_id: int) -> str | None:
    """The credential reference of `user_id`'s community identity, or None before enrollment."""
    return db.scalar(
        select(CommunityIdentityRow.credential_ref).where(CommunityIdentityRow.user_id == user_id)
    )


def ensure_identity_ref(db: Session, user_id: int) -> str:
    """Reserve a home for `user_id`, committing a new random reference when it has none.

    A concurrent reservation for the same account loses on the unique `user_id` and
    reads the winner's reference, so an account never holds two homes.
    """
    existing = identity_ref(db, user_id)
    if existing is not None:
        return existing
    if db.get(UserRow, user_id) is None:
        raise IdentityUnavailable("unavailable", "Community identities belong to an account")
    db.add(CommunityIdentityRow(credential_ref=uuid.uuid4().hex, user_id=user_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    reference = identity_ref(db, user_id)
    if reference is None:
        raise IdentityUnavailable("unavailable", "Unable to reserve a community identity")
    return reference


def adopt_owner_identity(db: Session, data_dir: Path) -> None:
    """Move upgraded or restored incoming files into the owner's home, and nowhere else.

    The incoming directory only ever carries the owner's identity, or the one awaiting
    the owner, so another account's resolution must not take those files.
    """
    owner = activity_owner(db)
    rows = db.scalars(select(CommunityIdentityRow)).all()
    for user_id in (owner.user_id if owner is not None else None, None):
        for row in rows:
            if row.user_id == user_id:
                adopt_incoming_identity(IdentityHome(Path(data_dir), row.credential_ref))
                return


def user_identity_home(
    db: Session, data_dir: Path, user_id: int, *, create: bool = False
) -> IdentityHome | None:
    """`user_id`'s identity home, or None when it has none and `create` is false.

    A session that held no transaction is left without one, so callers that require
    a clean session can resolve first.
    """
    clean = not db.in_transaction()
    adopt_owner_identity(db, data_dir)
    reference = ensure_identity_ref(db, user_id) if create else identity_ref(db, user_id)
    if clean and db.in_transaction():
        db.rollback()
    return IdentityHome(Path(data_dir), reference) if reference is not None else None


def identity_connection(
    db: Session, credential_ref: str, service_url: str, *, populate_existing: bool = False
) -> CommunityConnectionRow | None:
    """One identity's connection to one service, if it exists."""
    return db.get(
        CommunityConnectionRow, (credential_ref, service_url), populate_existing=populate_existing
    )


def load_local_identity(db: Session, home: IdentityHome, *, recovery=False):
    """Do not replace a missing key when the database remembers enrollment."""
    enrolled = (
        db.query(CommunityConnectionRow)
        .filter(
            CommunityConnectionRow.credential_ref == home.credential_ref,
            CommunityConnectionRow.contributor_id.is_not(None)
            | CommunityConnectionRow.key_id.is_not(None),
        )
        .all()
    )
    if enrolled and not (home.root / "identity.json").is_file():
        raise IdentityUnavailable("incomplete", "Restore the community identity from backup")
    if recovery:
        from scanner.security.community_key_recovery import recovery_source

        identity = recovery_source(home)
    else:
        identity = load_or_create_installation_identity(home)
    for row in enrolled:
        if not projects_identity(
            identity,
            installation_id=row.installation_id,
            contributor_id=row.contributor_id,
            service_url=row.service_url,
            key_id=row.key_id,
        ):
            raise IdentityUnavailable("corrupt", "Database identity does not match the signing key")
    return identity


def set_connection(
    db: Session,
    *,
    credential_ref: str,
    service_url: str,
    contributor_id: str | None,
    installation_id: str | None,
    key_id: str | None,
    sharing_enabled: bool = False,
    consent_version: str | None = None,
    consented_at: str | None = None,
) -> CommunityConnectionRow:
    """Persist queryable identity metadata, without storing private signing material."""
    if sharing_enabled and not (
        contributor_id and installation_id and key_id and consent_version and consented_at
    ):
        raise ValueError("Sharing requires identity and recorded consent")
    row = db.get(CommunityConnectionRow, (credential_ref, service_url))
    if row is None:
        row = CommunityConnectionRow(credential_ref=credential_ref, service_url=service_url)
        db.add(row)
    row.contributor_id = contributor_id
    row.installation_id = installation_id
    row.key_id = key_id
    row.sharing_enabled = sharing_enabled
    row.consent_version = consent_version
    row.consented_at = consented_at
    db.flush()
    return row


def find_import(
    db: Session, *, service_url: str, revision_id: str, digest: str
) -> CommunityImportRow | None:
    """Find a previously committed artifact before creating another local scan."""
    return (
        db.query(CommunityImportRow)
        .filter_by(service_url=service_url, revision_id=revision_id, digest=digest)
        .one_or_none()
    )


def attach_import(
    db: Session,
    *,
    scan_id: str,
    service_url: str,
    contribution_id: str,
    revision_id: str,
    digest: str,
    author_id: str,
    artifact_type: str,
) -> CommunityImportRow:
    """Stage provenance beside an imported scan in the caller's transaction.

    Call find_import before creating the scan, and commit the scan plus this row
    together. A duplicate artifact linked to another scan must reuse that scan.
    """
    scan = db.get(ScanRow, scan_id)
    if scan is None or scan.origin != "imported":
        raise ValueError("Community provenance requires an imported scan")
    existing = find_import(db, service_url=service_url, revision_id=revision_id, digest=digest)
    if existing is not None:
        if (
            existing.scan_id,
            existing.contribution_id,
            existing.author_id,
            existing.artifact_type,
        ) != (scan_id, contribution_id, author_id, artifact_type):
            raise ValueError("Import already linked to another scan or conflicting metadata")
        return existing
    row = CommunityImportRow(
        id=str(uuid.uuid4()),
        scan_id=scan_id,
        service_url=service_url,
        contribution_id=contribution_id,
        revision_id=revision_id,
        digest=digest,
        author_id=author_id,
        artifact_type=artifact_type,
    )
    db.add(row)
    db.flush()
    return row


def enqueue_operation(
    db: Session,
    *,
    credential_ref: str,
    operation_id: str,
    service_url: str,
    kind: str,
    contributor_id: str,
    installation_id: str,
    revision_id: str | None = None,
    digest: str | None = None,
    contribution_id: str | None = None,
    scan_id: str | None = None,
    consent_version: str | None = None,
    receipt: str | None = None,
    vote: str | None = None,
    authored_review_id: str | None = None,
    actor_id: int | None = None,
    community_version_id: str | None = None,
    filter_id: str | None = None,
    receipt_id: str | None = None,
) -> CommunityOperationRow:
    """Persist an exact retry intent of the identity `credential_ref` names.

    `publish` and `submission` share approved bytes under active consent. A
    `filter_vote` names the version, filter and snapshot receipt it votes with and
    keeps the exact vote JSON in `receipt`. The operation's history belongs to that
    identity's account. Duplicate IDs cannot change its identity.
    """
    if not operation_id or kind not in {"publish", "submission", "filter_vote"}:
        raise ValueError("Invalid community operation")
    identity = db.get(CommunityIdentityRow, credential_ref)
    if identity is None:
        raise ValueError("Operation identity does not match the local connection")
    connection = identity_connection(db, credential_ref, service_url)
    if connection is None or (connection.contributor_id, connection.installation_id) != (
        contributor_id,
        installation_id,
    ):
        raise ValueError("Operation identity does not match the local connection")
    if kind in ("publish", "submission"):
        if not (
            connection.sharing_enabled
            and consent_version == connection.consent_version
            and (scan_id or (authored_review_id and db.get(SavedReviewRow, authored_review_id)))
            and digest
        ):
            raise ValueError("Publishing requires active sharing consent, scan and digest")
    elif not (
        community_version_id
        and filter_id
        and receipt_id
        and receipt
        and vote in {"up", "down", "none"}
    ):
        raise ValueError("A filter vote requires its version, filter, receipt and vote")
    values = dict(
        service_url=service_url,
        kind=kind,
        contributor_id=contributor_id,
        installation_id=installation_id,
        contribution_id=contribution_id,
        revision_id=revision_id,
        digest=digest,
        scan_id=scan_id,
        consent_version=consent_version,
        receipt=receipt,
        vote=vote,
        community_version_id=community_version_id,
        filter_id=filter_id,
        receipt_id=receipt_id,
    )
    row = db.get(CommunityOperationRow, operation_id)
    if row is not None:
        if any(getattr(row, name) != value for name, value in values.items()):
            raise ValueError("Operation ID already belongs to a different intent")
        return row
    row = CommunityOperationRow(
        operation_id=operation_id,
        state="pending",
        updated_at=datetime.now(timezone.utc).isoformat(),
        owner_id=identity.user_id,
        actor_id=identity.user_id if actor_id is None else actor_id,
        **values,
    )
    db.add(row)
    db.flush()
    return row


def set_operation_state(db: Session, *, operation_id: str, state: str) -> CommunityOperationRow:
    """Advance an existing retry without changing its remote IDs or payload."""
    row = db.get(CommunityOperationRow, operation_id)
    if row is None or state not in {"pending", "submitted", "acknowledged", "failed"}:
        raise ValueError("Unknown operation or state")
    if row.state == "acknowledged" and state != "acknowledged":
        raise ValueError("Acknowledged operations cannot be reopened")
    row.state = state
    row.updated_at = datetime.now(timezone.utc).isoformat()
    db.flush()
    return row


def pending_after_restore(
    db: Session,
    *,
    credential_ref: str,
    service_url: str,
    contributor_id: str,
    installation_id: str,
    key_id: str,
) -> list[CommunityOperationRow]:
    """List recoverable intents only when restored credentials still match.

    Callers must verify remote state with the same operation ID before retrying;
    submitted operations may already have succeeded remotely.
    """
    connection = identity_connection(db, credential_ref, service_url)
    if connection is None or (
        connection.contributor_id,
        connection.installation_id,
        connection.key_id,
    ) != (contributor_id, installation_id, key_id):
        return []
    rows = (
        db.query(CommunityOperationRow)
        .filter(
            CommunityOperationRow.service_url == service_url,
            CommunityOperationRow.contributor_id == contributor_id,
            CommunityOperationRow.installation_id == installation_id,
            CommunityOperationRow.state.in_(("pending", "submitted", "failed")),
        )
        .order_by(CommunityOperationRow.updated_at, CommunityOperationRow.operation_id)
        .all()
    )
    return [
        row
        for row in rows
        if row.kind not in ("publish", "submission")
        or (connection.sharing_enabled and row.consent_version == connection.consent_version)
    ]
