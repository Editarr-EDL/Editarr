"""Live source and contributor authority for community activities."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select

from scanner.db import (
    AuthSessionRow,
    CommunityIdentityRow,
    SavedReviewRow,
    ScanRow,
    UserRow,
)
from scanner.persistence.repository.repository_community import identity_connection
from scanner.persistence.repository.review_access import (
    VIEW,
    ReviewAccess,
    not_found,
    review_facts,
)
from scanner.persistence.repository.scan_jobs import ScanMedia, scan_job_facts
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.identity import UserId
from scanner.security.access.records import AuditEvent, AuditOutcome, MediaAllowed
from scanner.security.access.targets import AccountRef, Action, ActionPair, MediaRef, ResourceRef
from scanner.security.access.vocabulary import Role
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import stage_event
from scanner.security.scan_jobs.media import decide_scans

from .donation_sources import SourceRef
from .transport import CommunityFailure

PUBLISH_REVIEW = ActionPair(C.COMMUNITY_PUBLISH_OWN_REVIEW, C.COMMUNITY_PUBLISH_OTHER_REVIEW)
CHECK = ActionPair(C.COMMUNITY_PUBLICATIONS_CHECK_OWN, C.COMMUNITY_PUBLICATIONS_CHECK_OTHER)
RETRY = ActionPair(C.COMMUNITY_PUBLICATIONS_RETRY_OWN, C.COMMUNITY_PUBLICATIONS_RETRY_OTHER)
RETRACT = ActionPair(C.COMMUNITY_PUBLICATIONS_RETRACT_OWN, C.COMMUNITY_PUBLICATIONS_RETRACT_OTHER)


def contributor_account(db, user_id: int) -> AccountRef:
    """The active account whose contributor sent existing work, which own/other resolves against."""
    user = db.get(UserRow, user_id)
    if user is None or user.status != "active":
        raise not_found()
    return AccountRef(UserId(user.id), Role(user.role))


@dataclass(frozen=True)
class ActivitySource:
    owner: AccountRef | None
    media: ScanMedia
    scans: tuple[str, ...]


@dataclass
class ActivityAccess:
    """Authorize one source and contributor against fresh account and media state."""

    reviews: ReviewAccess
    source: SourceRef | ScanMedia
    action: Action
    contributor_owner: int | None = None
    credential_ref: str | None = None
    connection_state: tuple | None = None
    service_url: str | None = None

    def __post_init__(self):
        if self.reviews.session_id is None:
            self.reviews.session_id = self.reviews.access.session_id

    @property
    def db(self):
        return self.reviews.db

    def facts(self) -> ActivitySource:
        if isinstance(self.source, ScanMedia):
            return ActivitySource(None, self.source, ())
        if self.source.kind == "review":
            facts = review_facts(self.db, self.source.identifier)
            if facts is None:
                raise not_found()
            return ActivitySource(facts.owner, facts.media, facts.memberships)
        scan = scan_job_facts(self.db, [self.source.identifier]).get(self.source.identifier)
        if scan is None or scan.status != "complete":
            raise not_found()
        return ActivitySource(None, scan.media, (scan.scan_id,))

    def _require(self, facts, evidence):
        if self.reviews.session_id is not None:
            now = datetime.now(UTC).replace(tzinfo=None)
            session = self.db.scalar(
                select(AuthSessionRow.id).where(
                    AuthSessionRow.id == self.reviews.session_id,
                    AuthSessionRow.user_id == self.reviews.access.principal.user_id,
                    AuthSessionRow.revoked_at.is_(None),
                    AuthSessionRow.absolute_expires_at > now,
                    or_(
                        AuthSessionRow.idle_expires_at.is_(None),
                        AuthSessionRow.idle_expires_at > now,
                    ),
                )
            )
            if session is None:
                raise AccessDenied(AccessErrorCode.AUTHENTICATION_REQUIRED)
        decision = decide_scans(
            self.db,
            self.reviews.access.principal.account,
            [facts.media],
            datetime.now(UTC),
            evidence=evidence,
        )[0]
        if not isinstance(decision, MediaAllowed):
            raise not_found()
        if isinstance(self.source, SourceRef) and self.source.kind == "review":
            if not isinstance(
                decide(self.reviews.access, VIEW, ResourceRef(facts.owner, decision)), Allowed
            ):
                raise not_found()
        elif isinstance(self.source, SourceRef):
            if not isinstance(
                decide(self.reviews.access, C.SCANS_FINDINGS_READ, MediaRef(decision)), Allowed
            ):
                raise not_found()
        owner = facts.owner
        if self.contributor_owner is not None:
            owner = contributor_account(self.db, self.contributor_owner)
        if isinstance(self.action, ActionPair):
            require(self.reviews.access, self.action, ResourceRef(owner, decision))
        elif self.action == C.COMMUNITY_FEEDBACK_SUBMIT:
            principal = self.reviews.access.principal
            require(
                self.reviews.access,
                self.action,
                AccountRef(principal.user_id, principal.account.role),
            )
        else:
            require(self.reviews.access, self.action, MediaRef(decision))
        if isinstance(self.source, SourceRef) and self.source.kind == "review":
            from scanner.persistence.repository.review_repairs import require_repaired

            require_repaired(self.db, self.source.identifier)
        if self.credential_ref is not None:
            identity = self.db.get(CommunityIdentityRow, self.credential_ref)
            connection = identity_connection(self.db, self.credential_ref, self.service_url)
            user = self.db.get(UserRow, identity.user_id) if identity is not None else None
            if user is None or user.status != "active":
                raise not_found()
            if (
                connection is None
                or (
                    identity.user_id,
                    connection.contributor_id,
                    connection.installation_id,
                    connection.key_id,
                )
                != self.connection_state
            ):
                raise CommunityFailure("identity_conflict")

    def run(self, operation):
        """Gather provider evidence unlocked, then decide and run under the write lock."""
        self.reviews.reload()
        if self.action not in (VIEW, C.SCANS_FINDINGS_READ):
            capabilities = (
                {self.action.own, self.action.other}
                if isinstance(self.action, ActionPair)
                else {self.action}
            )
            if self.reviews.access.capabilities.isdisjoint(capabilities):
                raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
        facts = self.facts()
        scans = scan_job_facts(self.db, facts.scans)
        if set(scans) != set(facts.scans):
            raise not_found()
        media = [facts.media, *(scan.media for scan in scans.values())]

        def validate(evidence):
            self._require(facts, evidence[0])

        with self.reviews.writing(media, validate) as evidence:
            if self.facts() != facts or scan_job_facts(self.db, facts.scans) != scans:
                raise not_found()
            self._require(facts, evidence[0])
            for scan, captured in zip(scans.values(), evidence[1:], strict=True):
                decision = decide_scans(
                    self.db,
                    self.reviews.access.principal.account,
                    [scan.media],
                    datetime.now(UTC),
                    evidence=captured,
                )[0]
                if not isinstance(
                    decide(self.reviews.access, C.SCANS_FINDINGS_READ, MediaRef(decision)), Allowed
                ):
                    raise not_found()
            result = operation()
            self.db.commit()
            return result

    def check(self):
        self.run(lambda: None)

    def audit(self, action: AuditAction, operation_id: str):
        """Stage secret-free publication attribution beside the admitted local change."""
        principal = self.reviews.access.principal
        facts = self.facts()
        is_review = isinstance(self.source, SourceRef) and self.source.kind == "review"
        kind = (
            "own_review"
            if is_review and facts.owner and facts.owner.user_id == principal.user_id
            else "other_review"
            if is_review
            else "scan_evidence"
        )
        target = (
            facts.owner.user_id
            if action == AuditAction.COMMUNITY_PUBLISH and is_review and facts.owner
            else self.contributor_owner
            if self.contributor_owner is not None
            else principal.user_id
        )
        row = self.db.get(SavedReviewRow if is_review else ScanRow, self.source.identifier)
        stage_event(
            self.db,
            AuditEvent(
                occurred_at=datetime.now(UTC),
                actor=principal.user_id,
                credential=principal.credential,
                action=action.value,
                outcome=AuditOutcome.SUCCEEDED,
                target_user=UserId(target),
                media_key=row.media_item_id,
                resource=operation_id,
                details={"kind": kind},
            ),
        )

    def bind(self, client):
        """Recheck authorization before every transport attempt, including retries."""
        self.credential_ref = client.home.credential_ref
        self.service_url = client.transport.endpoint.url
        identity = self.db.get(CommunityIdentityRow, self.credential_ref)
        connection = identity_connection(self.db, self.credential_ref, self.service_url)
        if identity is None or connection is None or not connection.contributor_id:
            self.db.rollback()
            raise CommunityFailure("not_connected")
        self.connection_state = (
            identity.user_id,
            connection.contributor_id,
            connection.installation_id,
            connection.key_id,
        )
        self.db.rollback()
        previous = client.transport.before_send

        def before_send():
            self.check()
            if previous is not None:
                previous()

        client.transport.before_send = before_send
        client.activity_access = self
        return client
