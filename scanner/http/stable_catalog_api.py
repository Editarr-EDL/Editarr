"""Resolve stable Editarr titles and legacy Jellyfin bookmarks under live access."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.db import MediaItemRow, SavedReviewRow, ScanRow, get_db
from scanner.integrations.jellyfin import JellyfinError
from scanner.persistence.repository import media_identity_store as identity
from scanner.persistence.repository.repository_catalog import get_scan, upsert_media_item
from scanner.persistence.repository.review_access import ReviewAccess
from scanner.persistence.write_lock import write_lock
from scanner.runtime.media_fingerprints import cached_content_digest
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied

from .catalog_access import CatalogViewer, catalog_viewer, jellyfin_record, not_found
from .catalog_library_api import (
    _with_library_item_state,
    authorized_jellyfin_item,
    jellyfin_item_target,
)
from .review_targets import capture_target
from .scan_access import ScanCaller
from .stable_media_capture import file_digest, observe_target_version

router = APIRouter()
_browse = catalog_viewer(C.MEDIA_BROWSE)
_CONTAINER_TYPES = {"Season", "Folder", "BoxSet", "CollectionFolder"}


class VersionTarget(BaseModel):
    item_id: str
    media_source_id: str | None = None


def _find_media(db, server_id, item_id):
    return db.query(MediaItemRow).filter_by(server_id=server_id, jellyfin_id=item_id).one_or_none()


def _observe(db, server_id, item, library_id):
    with write_lock(db):
        existing = _find_media(db, server_id, item.id)
        media = upsert_media_item(
            db,
            server_id=server_id,
            jellyfin_id=item.id,
            library_id=item.library_id or library_id or (existing.library_id if existing else None),
            item_type=item.item_type,
            name=item.name,
            path=item.path,
            commit=False,
        )
        title_id = identity.observe_title(db, media, item.media_identity)
        db.commit()
        return title_id


def _live_item(viewer, credentials, item_id):
    try:
        item, library = authorized_jellyfin_item(viewer, credentials, item_id)
    except AccessDenied:
        return None
    except JellyfinError as exc:
        raise HTTPException(502, str(exc)) from exc
    return item.model_copy(update={"library_id": item.library_id or library})


def _candidate(viewer, credentials, title_id, anchor):
    """Find a current binding for a stored title and verify provider identity and access."""
    if not anchor or not identity.title_has_aliases(viewer.db, title_id):
        return []

    def reachable(hits, library_id):
        matching = [
            hit
            for hit in hits
            if hit.id != anchor.jellyfin_id
            and hit.media_identity
            and identity.identity_matches_title(viewer.db, title_id, hit.media_identity)
        ]
        return viewer.visible(
            matching, lambda item: jellyfin_item_target(viewer, credentials, item, library_id)
        )

    def read(client, library_id, title=""):
        return client.all_items(
            library_id,
            recursive=not title or anchor.item_type == "Episode",
            include_item_types=(anchor.item_type,),
            search_term=title,
        )

    # Search by the last known name first, then compare typed provider metadata.
    try:
        with root.JellyfinClient(*credentials) as client:
            home = anchor.library_id
            if home and viewer.library_visible(home):
                try:
                    found = reachable(read(client, home, anchor.name), home)
                except JellyfinError:
                    found = []
                if found:
                    return found

            other_libraries = [
                library.id
                for library in client.libraries()
                if library.id != home and viewer.library_visible(library.id)
            ]
            found = []
            for library_id in other_libraries:
                found.extend(reachable(read(client, library_id, anchor.name), library_id))
            if found:
                return list({item.id: item for item in found}.values())

            # Titles can be renamed. Restrict the broader search to the original movie
            # or series library, where a provider match can still be checked precisely.
            if home and anchor.item_type in {"Movie", "Series"} and viewer.library_visible(home):
                return reachable(read(client, home), home)
    except JellyfinError:
        return []
    return []


def _history(db, viewer, versions, media_by_id):
    """Project only the historical scans and reviews visible under current authority."""
    version_ids = {version.id for version in versions}
    media_ids = set(media_by_id)
    rows = (
        db.query(ScanRow)
        .filter(ScanRow.media_item_id.in_(media_ids))
        .order_by(ScanRow.created_at.desc())
        .all()
    )
    scan_caller = ScanCaller(viewer.access, db, datetime.now(UTC))
    scan_versions = {row.id: identity.scan_version_id(db, row.id) for row in rows}
    allowed = scan_caller.seen(list(scan_versions))
    reviews = viewer.export_caller
    scan_lists = {version_id: [] for version_id in version_ids}
    review_lists = {version_id: [] for version_id in version_ids}
    for row in rows:
        version_id = scan_versions[row.id]
        if version_id in version_ids and row.id in allowed:
            scan_lists[version_id].append(
                scan_caller.present(get_scan(db, row.id), allowed[row.id])
            )
    for review in db.query(SavedReviewRow).filter(SavedReviewRow.media_item_id.in_(media_ids)):
        version_id = identity.review_version_id(db, review.id)
        if version_id not in version_ids:
            continue
        try:
            reviews.visible(review.id)
        except AccessDenied:
            continue
        review_lists[version_id].append({"id": review.id, "name": review.name})
    return scan_lists, review_lists


def _source_generations(db, live, requested):
    """Find verified live versions without putting full file reads on ordinary title loads."""
    current = {}
    for item in live:
        sources = [(entry.id, entry.path) for entry in item.versions if entry.path]
        if not sources and item.path:
            sources = [(None, item.path)]
        for source_id, source_path in sources:
            try:
                path = root.resolve_local_path(db, source_path).local_path
                digest = cached_content_digest(path)
                if digest is None and requested and requested.content_sha256:
                    # An explicit version bookmark may need its physical file revalidated.
                    digest = file_digest(path)
            except (ValueError, HTTPException):
                continue
            if digest:
                current[(item.id, source_id)] = digest
    return current


def _resolve(db, viewer, credentials, title_id, *, preferred=None, original=None, version_id=None):
    records = identity.title_media_items(db, title_id)
    live = []
    if preferred is not None and identity.identity_conflicts_title(
        db,
        title_id,
        preferred.media_identity,
    ):
        preferred = None
    if preferred is not None:
        live.append(preferred)
    for row in records:
        if row.server_id == credentials[0] and (
            preferred is None or row.jellyfin_id != preferred.id
        ):
            current = _live_item(viewer, credentials, row.jellyfin_id)
            if current is not None and not identity.identity_conflicts_title(
                db,
                title_id,
                current.media_identity,
            ):
                live.append(
                    current.model_copy(update={"library_id": current.library_id or row.library_id})
                )
    if not live:
        anchors = [original] if original else records
        for anchor in anchors:
            recovered = _candidate(viewer, credentials, title_id, anchor)
            if recovered:
                for item in recovered:
                    matches = _observe(db, credentials[0], item, anchor.library_id)
                    if matches == title_id:
                        live.append(item)
                break

    records = identity.title_media_items(db, title_id)
    media_by_id = {row.id: row for row in records}
    versions = identity.title_versions(db, title_id)
    scan_lists, review_lists = _history(db, viewer, versions, media_by_id)
    active_ids = {item.id for item in live}
    requested = next((version for version in versions if version.id == version_id), None)
    digests = _source_generations(db, live, requested)
    current_versions = {}
    for version in versions:
        if version.content_sha256:
            for target, checksum in digests.items():
                if version.content_sha256 == checksum:
                    current_versions[target] = version.id
    historical = []
    original_choices = []
    for version in versions:
        locators = [
            loc
            for loc in identity.version_locators(db, version.id)
            if loc.media_item_id in media_by_id
        ]
        if not locators:
            continue
        loc = next(
            (entry for entry in locators if original and entry.media_item_id == original.id),
            locators[0],
        )
        media = media_by_id[loc.media_item_id]
        scans, reviews = scan_lists[version.id], review_lists[version.id]
        if not (viewer.is_owner or scans or reviews):
            continue
        exact_target = next(
            (target for target, current in current_versions.items() if current == version.id), None
        )
        label = next((scan.media_source_name for scan in scans if scan.media_source_name), None)
        historical.append(
            {
                "id": version.id,
                "jellyfin_item_id": media.jellyfin_id,
                "media_source_id": loc.media_source_id or None,
                "label": label or media.name,
                "available": exact_target is not None,
                "active_item_id": exact_target[0] if exact_target else None,
                "active_media_source_id": exact_target[1] if exact_target else None,
                "scans": scans,
                "reviews": reviews,
            }
        )
        if original and loc.media_item_id == original.id:
            original_choices.append(version.id)
    if not live and not historical:
        raise not_found()
    live = [
        jellyfin_record(item, viewer)
        for item in _with_library_item_state(
            db, server_id=credentials[0], items=live, caller=viewer.export_caller
        )
    ]
    active_item = live[0] if len(active_ids) == 1 or preferred is not None else None
    db.commit()
    return {
        "title_id": title_id,
        "name": active_item.name
        if active_item
        else live[0].name
        if live
        else historical[0]["label"],
        "active_item": active_item,
        "active_items": live,
        "versions": historical,
        "current_versions": [
            {"item_id": item_id, "media_source_id": source_id, "version_id": exact_id}
            for (item_id, source_id), exact_id in current_versions.items()
        ],
        "selected_version_id": original_choices[0] if len(original_choices) == 1 else None,
        "choose_version": len(original_choices) > 1,
    }


def _credentials(db):
    configured = root.get_jellyfin_credentials(db)
    if not configured:
        raise HTTPException(409, "Configure Jellyfin before opening media")
    return configured


@router.get("/api/editarr-items/legacy/{item_id}")
def legacy_item(
    item_id: str,
    library_id: str | None = None,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(_browse),
):
    """Resolve an old bookmark from saved bindings, including non-current Jellyfin IDs."""
    credentials = _credentials(db)
    direct = _live_item(viewer, credentials, item_id)
    if direct is not None:
        if direct.item_type in _CONTAINER_TYPES:
            raise not_found()
        title_id = _observe(db, credentials[0], direct, library_id)
        return _resolve(db, viewer, credentials, title_id, preferred=direct)
    old = _find_media(db, credentials[0], item_id)
    if old is None:
        raise not_found()
    title_id = identity.find_title_id(db, server_id=credentials[0], jellyfin_id=item_id)
    if not title_id:
        raise not_found()
    return _resolve(db, viewer, credentials, title_id, original=old)


@router.get("/api/editarr-items/{title_id}")
def stable_item(
    title_id: str,
    version_id: str | None = None,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(_browse),
):
    """Look up a stable title, projecting only the viewer's reachable bindings."""
    return _resolve(db, viewer, _credentials(db), title_id, version_id=version_id)


@router.post("/api/editarr-items/{title_id}/versions")
def stable_version(
    title_id: str,
    target: VersionTarget,
    db: Session = Depends(get_db),
    viewer: CatalogViewer = Depends(_browse),
):
    """Assign a version ID to a selected, authorized and readable live source."""
    credentials = _credentials(db)
    if identity.find_title_id(db, server_id=credentials[0], jellyfin_id=target.item_id) != title_id:
        raise not_found()
    caller = ReviewAccess(db, viewer.access, viewer.session_id)
    prepared = capture_target(caller, target)
    if prepared.source.media_source_id != target.media_source_id:
        raise not_found()
    version_id = observe_target_version(db, prepared)
    db.commit()
    return {"version_id": version_id}
