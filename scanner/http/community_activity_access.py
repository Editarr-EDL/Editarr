"""Resolve activity authority before choosing whose contributor signs a request."""


from scanner.db import (
    CommunityOperationRow,
    CommunityPublicationRow,
    SavedReviewRow,
    ScanRow,
)
from scanner.integrations.community.activity_access import (
    CHECK,
    RETRACT,
    ActivityAccess,
    contributor_account,
)
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.donation_sources import SourceRef
from scanner.persistence.archive import is_archived
from scanner.persistence.repository.repository_community import user_identity_home
from scanner.persistence.repository.review_access import ReviewAccess, not_found
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import Allowed
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
