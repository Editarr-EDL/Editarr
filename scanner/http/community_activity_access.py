"""Resolve activity authority before choosing whose contributor signs a request."""

import json

from scanner.db import (
    CommunityImportRow,
    CommunityImportTargetRow,
    CommunityOperationRow,
    CommunityPublicationRow,
    SavedReviewRow,
    ScanRow,
)
from scanner.integrations.community.activity_access import (
    CHECK,
    RECEIPTS,
    RETRACT,
    ActivityAccess,
    ImportTarget,
    contributor_account,
)
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.donation_sources import SourceRef
from scanner.persistence.archive import is_archived
from scanner.persistence.repository.repository_community import user_identity_home
from scanner.persistence.repository.review_access import ReviewAccess, not_found
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.authorization import require, resolve_account
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.targets import NO_MEDIA, ResourceRef
from scanner.settings.config import settings


def source_client(db, access, source, action, *, contributor_owner=None):
    """New work uses the actor's identity; existing work names its original owner."""
    authority = ActivityAccess(ReviewAccess(db, access), source, action, contributor_owner)
    authority.check()
    client = _contributor_client(db, access, contributor_owner)
    if action == RETRACT:
        client.activity_access = authority
        db.rollback()
        return client
    return authority.bind(client)


def _contributor_client(db, access, contributor_owner):
    from .community_identity_api import local_client, service_transport

    if contributor_owner is None or contributor_owner == access.principal.user_id:
        return local_client(db, access)
    home = user_identity_home(db, settings.data_dir, contributor_owner)
    if home is None:
        raise not_found()
    return CommunityClient(db, home, service_transport())


def _archived_publication_client(db, access, owner_id, action):
    """An archive viewer may only check what a sent publication of archived work became.

    Archived work has no live source or media scope, so the archive's gate replaces
    the media check, and the contributor's account still decides own or other.
    """
    if action != CHECK or not isinstance(decide(access, C.ARCHIVE_VIEW), Allowed):
        raise not_found()
    require(access, CHECK, ResourceRef(contributor_account(db, owner_id), NO_MEDIA))
    client = _contributor_client(db, access, owner_id)
    db.rollback()
    return client


def publication_client(db, access, operation_id, action):
    """Management uses the persisted contributor and the source's live media scope."""
    operation = db.get(CommunityOperationRow, operation_id)
    payload = db.get(CommunityPublicationRow, operation_id)
    if (
        operation is None
        or payload is None
        or operation.kind not in ("submission", "publish")
        or operation.owner_id is None
    ):
        raise not_found()
    source_row = ScanRow if payload.source_kind == "scan" else SavedReviewRow
    if is_archived(db, source_row, payload.source_id):
        return _archived_publication_client(db, access, operation.owner_id, action)
    return source_client(
        db,
        access,
        SourceRef(payload.source_kind, payload.source_id),
        action,
        contributor_owner=operation.owner_id,
    )


def import_authority(db, access, imported, action, contributor_owner=None):
    """Select a verified copy whose association and media remain authorized under the lock."""
    targets = (
        db.query(CommunityImportTargetRow.media_item_id, CommunityImportTargetRow.media_source_id)
        .filter_by(import_id=imported.id)
        .order_by(CommunityImportTargetRow.media_item_id, CommunityImportTargetRow.media_source_id)
        .all()
    )
    for media_item_id, media_source_id in targets:
        authority = ActivityAccess(
            ReviewAccess(db, access),
            ImportTarget(imported.id, media_item_id, media_source_id),
            action,
            contributor_owner,
        )
        try:
            authority.check()
            return authority
        except AccessDenied as error:
            db.rollback()
            if error.code != AccessErrorCode.NOT_FOUND:
                raise
    raise not_found()


def receipt_client(db, access, user_id=None):
    """An explicit recovery target, with media checked separately for each import."""
    user_id = int(access.principal.user_id) if user_id is None else user_id
    account = resolve_account(db, access, user_id, RECEIPTS)
    capability = RECEIPTS.own if user_id == access.principal.user_id else RECEIPTS.other
    if capability not in access.capabilities:
        raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
    db.rollback()
    client = _contributor_client(db, access, int(account.user_id))

    def authorize_import(import_id):
        imported = db.get(CommunityImportRow, import_id)
        if imported is None:
            raise not_found()
        connection = client.connection()
        if connection is None or not connection.contributor_id:
            raise not_found()
        operation = (
            db.query(CommunityOperationRow)
            .filter_by(
                kind="receipt",
                owner_id=user_id,
                contributor_id=connection.contributor_id,
                service_url=imported.service_url,
                scan_id=imported.scan_id,
                revision_id=imported.revision_id,
                digest=imported.digest,
            )
            .one_or_none()
        )
        original = json.loads(imported.receipt_json) if imported.receipt_json else None
        if operation is None and (
            original is None or original["receipt"]["contributorId"] != connection.contributor_id
        ):
            raise not_found()
        authority = import_authority(db, access, imported, RECEIPTS, user_id)
        client.transport.before_send = client._before_send
        return authority.bind(client).activity_access

    client.authorize_import = authorize_import
    client.actor_id = int(access.principal.user_id)
    return client


def target_client(db, access, action):
    """Bind a provider-resolved target before local capture or remote lookup."""
    from .community_identity_api import local_client

    client = local_client(db, access)

    def authorize_target(media):
        authority = ActivityAccess(ReviewAccess(db, access), media, action)
        authority.check()
        client.transport.before_send = client._before_send
        return authority.bind(client)

    client.authorize_target = authorize_target
    return client


def vote_client(db, access):
    """Votes always use the actor's contributor and an accessible imported revision."""
    from .community_identity_api import local_client

    client = local_client(db, access)

    def authorize_revision(revision_id):
        imported = (
            db.query(CommunityImportRow)
            .filter_by(service_url=client.transport.endpoint.url, revision_id=revision_id)
            .one_or_none()
        )
        if imported is None:
            raise not_found()
        authority = import_authority(db, access, imported, C.COMMUNITY_FEEDBACK_SUBMIT)
        client.transport.before_send = client._before_send
        return authority.bind(client).activity_access

    client.authorize_revision = authorize_revision
    return client
