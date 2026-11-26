"""Actor-scoped review decisions with provider reads outside SQLite's write lock."""

from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from scanner.db import MediaItemRow, SavedReviewRow, UserRow
from scanner.persistence.write_lock import write_lock
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.authorization import require, require_principal
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied, Allowed
from scanner.security.access.evaluator import decide
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.identity import UserId
from scanner.security.access.targets import AccountRef, ActionPair, MediaRef, ResourceRef
from scanner.security.access.vocabulary import Role
from scanner.security.access.write_boundary import fresh_authority_write
from scanner.security.scan_jobs.media import decide_scans, gather_evidence
from scanner.security.scan_jobs.state import load_authority

from .activity_ownership import ActivityOwner
from .repository_paths import resolve_local_path
from .review_version_evidence import capture_review_media_proof, use_review_media_proof
from .scan_jobs import ScanMedia, scan_job_facts

VIEW = ActionPair(C.REVIEWS_VIEW_OWN, C.REVIEWS_VIEW_OTHER)
SAVE = ActionPair(C.REVIEWS_SAVE_OWN, C.REVIEWS_EDIT_OTHER)
RENAME = ActionPair(C.REVIEWS_RENAME_OWN, C.REVIEWS_EDIT_OTHER)
COPY = ActionPair(C.REVIEWS_DUPLICATE_OWN, C.REVIEWS_COPY_OTHER)
DELETE = ActionPair(C.REVIEWS_DELETE_OWN, C.REVIEWS_DELETE_OTHER)
PREVIEW = ActionPair(C.REVIEWS_PREVIEW_OWN, C.REVIEWS_PREVIEW_OTHER)
TIMING = ActionPair(C.REVIEWS_TIMING_EDIT_OWN, C.REVIEWS_TIMING_EDIT_OTHER)
CREDITS = ActionPair(C.REVIEWS_CREDITS_EDIT_OWN, C.REVIEWS_CREDITS_EDIT_OTHER)
EXPORT = ActionPair(C.EXPORTS_PERSONAL_PUBLISH_OWN, C.REVIEWS_EXPORT_OTHER)
CONTENT_EDITS = (
    C.REVIEWS_EVIDENCE_EDIT_OWN,
    C.REVIEWS_FILTERS_EDIT_OWN,
    C.REVIEWS_CUES_EDIT_OWN,
    C.REVIEWS_METADATA_EDIT_OWN,
    C.REVIEWS_TIMING_EDIT_OWN,
)


@dataclass(frozen=True)
class ReviewFacts:
    owner: AccountRef | None
    media: ScanMedia
    memberships: tuple[str, ...]


class MissingReviewScan(ValueError):
    """A selected review source scan is absent, hidden, or unusable."""


def media_facts(db: Session, media: MediaItemRow) -> ScanMedia:
    try:
        local_path = resolve_local_path(db, media.path).local_path if media.path else ""
    except ValueError:
        local_path = ""
    return ScanMedia(media.server_id, media.jellyfin_id, media.library_id, local_path)


def review_facts(db: Session, review_id: str) -> ReviewFacts | None:
    row = db.get(SavedReviewRow, review_id)
    if row is None:
        return None
    owner = db.get(UserRow, row.owner_id) if row.owner_id is not None else None
    return ReviewFacts(
        AccountRef(UserId(owner.id), Role(owner.role)) if owner is not None else None,
        media_facts(db, row.media_item),
        tuple(sorted(value.scan_id for value in row.memberships)),
    )


def not_found() -> AccessDenied:
    return AccessDenied(AccessErrorCode.NOT_FOUND)


@dataclass
class ReviewAccess:
    """Authorize a repository operation for one live request principal."""

    db: Session
    access: PrincipalAccess
    session_id: int | None = None

    def reload(self) -> None:
        principal = self.access.principal
        self.access = require_principal(
            self.db, principal.user_id, principal.credential, self.session_id
        )

    @contextmanager
    def writing(self, media, validate=None):
        """Bind every captured media answer to its locked account and policy state."""
        principal = self.access.principal

        def state():
            return tuple(
                load_authority(
                    self.db,
                    principal.user_id,
                    item,
                    datetime.now(UTC),
                    credential=principal.credential,
                    session_id=self.session_id,
                )
                for item in media
            )

        def prepare():
            before = state()
            if before[0].access is None:
                self.reload()
            else:
                self.access = before[0].access
            evidence = tuple(
                gather_evidence(self.db, self.access.principal.account, item, datetime.now(UTC))
                for item in media
            )
            if validate is not None:
                validate(evidence)
            return before, evidence

        with fresh_authority_write(self.db, prepare, state, not_found, lock=write_lock) as evidence:
            self.reload()
            yield evidence

    def media_decision(self, media: ScanMedia, *, evidence=None):
        """Decide captured media with the request authority's clock and live principal."""
        return decide_scans(
            self.db,
            self.access.principal.account,
            [media],
            datetime.now(UTC),
            evidence=evidence,
        )[0]

    def ref(self, facts: ReviewFacts, *, evidence=None) -> ResourceRef:
        return ResourceRef(
            facts.owner,
            self.media_decision(facts.media, evidence=evidence),
            existence_visible=False,
        )

    def visible(self, review_id: str) -> ReviewFacts:
        facts = review_facts(self.db, review_id)
        if facts is None or not isinstance(decide(self.access, VIEW, self.ref(facts)), Allowed):
            raise not_found()
        return facts

    def run(
        self,
        review_id: str,
        action,
        operation: Callable,
        *args,
        scan_ids=(),
        draft=None,
        prepared=None,
        allow_jobs=False,
        repair=False,
        extra_capabilities=(),
        authorize: Callable[[ResourceRef], None] | None = None,
        capture_review_media: bool = False,
        store_prepared: bool = True,
        **kwargs,
    ):
        """Run under live locked authority, optionally proving the prepared file before locking.

        Read-only previews can authorize a prepared target without storing its catalog mapping.
        """
        facts = self.visible(review_id)
        if action != DELETE and not repair:
            from .review_repairs import require_repaired

            require_repaired(self.db, review_id)
        requested = set(scan_ids) | set(facts.memberships)
        scans = scan_job_facts(self.db, list(requested))
        if set(scans) != requested:
            raise not_found()
        media = [facts.media]
        if prepared is not None:
            media.append(prepared.media)
        media.extend(item.media for item in scans.values())
        proof = None

        def validate(captured):
            nonlocal proof
            target = self.ref(facts, evidence=captured[0])
            if not isinstance(decide(self.access, VIEW, target), Allowed):
                raise not_found()
            require(self.access, action, target)
            for capability in extra_capabilities:
                require(self.access, capability, MediaRef(target.media))
            if capture_review_media:
                self._require_review_sources(
                    facts, prepared, scans, captured[1:], action, allow_jobs=allow_jobs
                )
                if authorize is not None:
                    authorize(target)
                if draft is not None:
                    self.require_changes(review_id, draft, target)
                path = (
                    prepared.media.resolved_path
                    if prepared is not None
                    else facts.media.resolved_path
                )
                proof = capture_review_media_proof(self.db, review_id, path, tuple(scans))

        with self.writing(media, validate) as captured:
            evidence, *scan_evidence = captured
            current = review_facts(self.db, review_id)
            if current != facts:
                raise not_found()
            target = self.ref(current, evidence=evidence)
            if not isinstance(decide(self.access, VIEW, target), Allowed):
                raise not_found()
            require(self.access, action, target)
            for capability in extra_capabilities:
                require(self.access, capability, MediaRef(target.media))
            if action != DELETE and not repair:
                require_repaired(self.db, review_id)
            if action == COPY:
                principal = self.access.principal
                destination = ResourceRef(
                    AccountRef(principal.user_id, principal.account.role), target.media
                )
                for capability in CONTENT_EDITS:
                    require(self.access, capability, destination)
                if self.db.get(SavedReviewRow, review_id).accepted_credits is not None:
                    require(self.access, C.REVIEWS_CREDITS_EDIT_OWN, destination)
            self._require_review_sources(
                current, prepared, scans, scan_evidence, action, allow_jobs=allow_jobs, recheck=True
            )
            if authorize is not None:
                authorize(target)
            if draft is not None:
                self.require_changes(review_id, draft, target)
            if prepared is not None and store_prepared:
                prepared.store(self.db)
            actor = ActivityOwner(
                int(self.access.principal.user_id), int(self.access.principal.permission_revision)
            )
            self.db.info["review_actor"] = actor
            try:
                with (
                    use_review_media_proof(self.db, proof)
                    if capture_review_media
                    else nullcontext()
                ):
                    return operation(self.db, review_id, *args, **kwargs)
            finally:
                self.db.info.pop("review_actor", None)

    def _require_review_sources(
        self, facts, prepared, scans, evidence, action, *, allow_jobs, recheck=False
    ):
        """Authorize the selected catalog file and every scan supplying evidence."""
        if prepared is not None:
            fresh_evidence, *evidence = evidence
            fresh = self.ref(replace(facts, media=prepared.media), evidence=fresh_evidence)
            require(self.access, VIEW, fresh)
            require(self.access, action, fresh)
        if recheck and scan_job_facts(self.db, scans) != scans:
            raise not_found()
        for item, captured_scan in zip(scans.values(), evidence, strict=True):
            media = decide_scans(
                self.db,
                self.access.principal.account,
                [item.media],
                datetime.now(UTC),
                evidence=captured_scan,
            )[0]
            self.require_scan(item, media, allow_jobs=allow_jobs)

    def require_changes(self, review_id: str, draft, target: ResourceRef) -> None:
        """A general save permission does not grant every kind of review edit."""
        from scanner.repository import get_saved_review

        stored = get_saved_review(self.db, review_id)
        changes = [
            ("name", C.REVIEWS_RENAME_OWN, C.REVIEWS_EDIT_OTHER),
            ("memberships", C.REVIEWS_EVIDENCE_EDIT_OWN, C.REVIEWS_EVIDENCE_EDIT_OTHER),
            ("mcf_document", C.REVIEWS_METADATA_EDIT_OWN, C.REVIEWS_METADATA_EDIT_OTHER),
            ("synchronization", C.REVIEWS_TIMING_EDIT_OWN, C.REVIEWS_TIMING_EDIT_OTHER),
            ("accepted_credits", C.REVIEWS_CREDITS_EDIT_OWN, C.REVIEWS_CREDITS_EDIT_OTHER),
        ]
        for field, own, other in changes:
            before, after = getattr(stored, field), getattr(draft, field)
            if field == "accepted_credits" and before is not None:
                before = before.model_dump(include=set(after.model_fields) if after else set())
                after = after.model_dump() if after else None
            if before != after:
                require(self.access, ActionPair(own, other), target)
        self._require_community_changes(stored, draft, target)
        from .review_policy import validate_policy_draft

        base_draft = validate_policy_draft(self.db, self.db.get(SavedReviewRow, review_id), draft)
        # Read-only provenance is not editable content and must not count as a change.
        before = {
            value.id: value.model_dump(exclude={"label_edited", "community"})
            for value in stored.base_filters
        }
        after = {value.id: value.model_dump() for value in base_draft}
        common = before.keys() & after.keys()
        cue_fields = {
            "edit_action",
            "mcf_channel",
            "mcf_comment",
            "label",
            "details",
            "group_key",
            "start_seconds",
            "end_seconds",
        }
        filter_changes = list(before) != list(after) or any(
            {key: value for key, value in before[filter_id].items() if key not in cue_fields}
            != {key: value for key, value in after[filter_id].items() if key not in cue_fields}
            for filter_id in common
        )
        cue_changes = bool(after.keys() - before.keys()) or any(
            any(before[filter_id][key] != after[filter_id][key] for key in cue_fields)
            for filter_id in common
        )
        if filter_changes or draft.new_manual_filters:
            require(
                self.access,
                ActionPair(C.REVIEWS_FILTERS_EDIT_OWN, C.REVIEWS_FILTERS_EDIT_OTHER),
                target,
            )
        if cue_changes or draft.new_manual_filters:
            require(
                self.access, ActionPair(C.REVIEWS_CUES_EDIT_OWN, C.REVIEWS_CUES_EDIT_OTHER), target
            )

    def _require_community_changes(self, stored, draft, target: ResourceRef) -> None:
        """Changing included community filters is an evidence edit; taking a snapshot
        (a new link or a refresh) also needs `community.search`."""
        requested = getattr(draft, "community_memberships", None)
        if requested is None:
            return
        captures = any(value.link_id or value.refresh for value in requested)
        before = [value.source_id for value in stored.community_sources]
        if captures or before != [value.source_id for value in requested]:
            require(
                self.access,
                ActionPair(C.REVIEWS_EVIDENCE_EDIT_OWN, C.REVIEWS_EVIDENCE_EDIT_OTHER),
                target,
            )
        if captures and C.COMMUNITY_SEARCH not in self.access.capabilities:
            raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)

    def require_scan(self, item, media, *, allow_jobs=False):
        """Completed findings are shared; unfinished diagnostics remain job-scoped."""
        from scanner.security.access.targets import MediaRef

        if item.status == "complete":
            require(self.access, C.SCANS_FINDINGS_READ, MediaRef(media))
        elif allow_jobs and item.initiator_id is not None and item.initiator_role is not None:
            owner = AccountRef(UserId(item.initiator_id), Role(item.initiator_role))
            require(
                self.access,
                ActionPair(C.SCAN_JOBS_VIEW_OWN, C.SCAN_JOBS_VIEW_OTHER),
                ResourceRef(owner, media),
            )
        else:
            raise not_found()

    def present(self, value):
        """Project host paths without changing stored review or scan evidence."""
        from .export_discovery import project_publications
        from .review_projection import project_review, verification_source_ids

        def visible(review_id):
            try:
                self.visible(review_id)
            except AccessDenied:
                return False
            return True

        projected = project_publications(self, value)
        sources = list(scan_job_facts(self.db, verification_source_ids(projected)).values())
        visible_sources = set()
        if sources:
            decisions = decide_scans(
                self.db,
                self.access.principal.account,
                [source.media for source in sources],
                datetime.now(UTC),
            )
            for source, decision in zip(sources, decisions, strict=True):
                try:
                    self.require_scan(source, decision)
                except AccessDenied:
                    continue
                visible_sources.add(source.scan_id)

        return project_review(
            projected,
            paths=C.MEDIA_PATHS_VIEW in self.access.capabilities,
            visible=visible,
            scan_visible=visible_sources.__contains__,
        )

    def _require_creation(self, decision, *, capability, capabilities, creating):
        principal = self.access.principal
        target = ResourceRef(AccountRef(principal.user_id, principal.account.role), decision)
        if creating:
            require(self.access, C.REVIEWS_CREATE_OWN, target)
        require(self.access, capability, target)
        for required in capabilities:
            require(self.access, required, target)

    def authorize_create(self, media: ScanMedia, *, capability, capabilities, scan_ids=()):
        """Check upload authority before reading media. Creation checks again under lock."""
        scans = scan_job_facts(self.db, scan_ids)
        decision, *source_decisions = decide_scans(
            self.db,
            self.access.principal.account,
            [media, *(item.media for item in scans.values())],
            datetime.now(UTC),
        )
        self._require_creation(
            decision, capability=capability, capabilities=capabilities, creating=True
        )
        if set(scans) != set(scan_ids):
            raise not_found()
        for item, source_decision in zip(scans.values(), source_decisions, strict=True):
            self.require_scan(item, source_decision)

    def create(
        self,
        media: ScanMedia,
        operation: Callable,
        *,
        scan_ids=(),
        capability=C.REVIEWS_CREATE_OWN,
        capabilities=(),
        creating=True,
        allow_jobs=False,
        missing_scan_error: Callable[[], Exception] = not_found,
    ):
        """Create actor-owned activity after gathering all media evidence before the lock."""
        scans = scan_job_facts(self.db, list(scan_ids))
        if set(scans) != set(scan_ids) and missing_scan_error is not_found:
            raise missing_scan_error()
        with self.writing([media, *(item.media for item in scans.values())]) as captured:
            evidence, *scan_evidence = captured
            principal = self.access.principal
            actor = ActivityOwner(int(principal.user_id), int(principal.permission_revision))
            decision = decide_scans(
                self.db, principal.account, [media], datetime.now(UTC), evidence=evidence
            )[0]
            self._require_creation(
                decision, capability=capability, capabilities=capabilities, creating=creating
            )
            if set(scans) != set(scan_ids):
                raise missing_scan_error()
            if scan_job_facts(self.db, list(scan_ids)) != scans:
                raise missing_scan_error()

            for item, captured_scan in zip(scans.values(), scan_evidence, strict=True):
                decision = decide_scans(
                    self.db,
                    principal.account,
                    [item.media],
                    datetime.now(UTC),
                    evidence=captured_scan,
                )[0]
                try:
                    self.require_scan(item, decision, allow_jobs=allow_jobs)
                except AccessDenied as exc:
                    if missing_scan_error is not not_found:
                        raise missing_scan_error() from exc
                    raise
            self.db.info["review_actor"] = actor
            try:
                result = operation(self.db)
                return result
            finally:
                self.db.info.pop("review_actor", None)
