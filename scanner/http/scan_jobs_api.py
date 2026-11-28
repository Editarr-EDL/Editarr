"""Scan job and history routes, each decided for the calling account."""


from fastapi import APIRouter, Depends, HTTPException

from scanner import api as root
from scanner.models import (
    ReviewError,
    Scan,
    ScanFindings,
    ScanNameUpdate,
    ScanQueueItem,
    ScanQueueReorder,
    ScanReferences,
)
from scanner.persistence.repository.repository_review_operations import scan_reference_facts
from scanner.persistence.repository.scan_jobs import queued_scan_id
from scanner.persistence.repository.scan_pause import pause_scan_queue_item
from scanner.repository import (
    ScanReferencedError,
    cancel_scan_queue_item,
    delete_scan,
    get_scan_findings,
    get_scan_references,
    list_scan_history,
    list_scan_queue,
    remove_scan_queue_item,
    rename_scan,
    reorder_scan_queue,
    resume_orphaned_scan,
    scan_worker_process,
    update_scan_status,
)
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import UserId
from scanner.security.access.targets import AccountRef, MediaRef, ResourceRef
from scanner.security.access.vocabulary import Role

from .scan_access import (
    JOB_CANCEL,
    JOB_RESUME,
    REVIEW_VIEW,
    SCAN_DELETE,
    SCAN_RENAME,
    ScanCaller,
    SeenScan,
    scan_caller,
)

router = APIRouter()


def _queued_job(caller: ScanCaller, queue_id: int, action) -> str:
    """The scan behind a queue entry this caller may act on; hidden jobs are not found."""

    scan_id = queued_scan_id(caller.db, queue_id)
    seen = caller.seen([scan_id]).get(scan_id) if scan_id is not None else None
    if seen is None or not seen.job:
        raise HTTPException(404, "Queued scan not found")
    caller.require(action, caller.ref(seen))
    return scan_id


@router.get("/api/scans/queue", response_model=list[ScanQueueItem])
def api_list_scan_queue(caller: ScanCaller = Depends(scan_caller)):
    return caller.visible(list_scan_queue(caller.db), jobs_only=True)


@router.post("/api/scans/queue/reorder")
def api_reorder_scan_queue(request: ScanQueueReorder, caller: ScanCaller = Depends(scan_caller)):
    """Order the jobs this caller sees; jobs hidden from it keep their places."""

    caller.require_reorder()
    movable = {item.queue_id for item in api_list_scan_queue(caller)}
    try:
        reorder_scan_queue(caller.db, request.queue_ids, movable)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.delete("/api/scans/queue/{queue_id}")
def api_cancel_scan_queue_item(queue_id: int, caller: ScanCaller = Depends(scan_caller)):
    """Cancel queued or active scan work and wake the queue processor."""
    db = caller.db
    _queued_job(caller, queue_id, JOB_CANCEL)
    worker = scan_worker_process(db, queue_id)
    cancelled = cancel_scan_queue_item(db, queue_id)
    if not cancelled:
        raise HTTPException(404, "Queued scan not found")
    scan_id, is_running = cancelled
    if is_running and worker and worker[3] == "orphaned":
        root._terminate_scan_worker(scan_id, *worker[:3])
        update_scan_status(db, scan_id, "cancelled")
        remove_scan_queue_item(db, queue_id)
        is_running = False
    if not is_running:
        from scanner.pipeline.visual_checkpoints import discard_visual_checkpoints

        discard_visual_checkpoints(root.settings.data_dir, scan_id)
    return {"cancelled": True, "cancelling": is_running}


@router.post("/api/scans/queue/{queue_id}/pause")
def api_pause_scan(queue_id: int, caller: ScanCaller = Depends(scan_caller)):
    """Persist a pause before acknowledging it to the caller."""
    _queued_job(caller, queue_id, JOB_RESUME)
    status = pause_scan_queue_item(caller.db, queue_id)
    if status is None:
        raise HTTPException(409, "Only a queued or running scan can be paused")
    return {"paused": status == "paused", "pausing": status == "pausing"}


@router.post("/api/scans/queue/{queue_id}/resume")
def api_resume_orphaned_scan(queue_id: int, caller: ScanCaller = Depends(scan_caller)):
    _queued_job(caller, queue_id, JOB_RESUME)
    if not resume_orphaned_scan(caller.db, queue_id):
        raise HTTPException(409, "Only a paused or interrupted scan can be resumed")
    root._start_scan_queue_worker_if_needed()
    return {"resumed": True}


@router.get("/api/scans/history", response_model=list[Scan])
def api_list_scan_history(caller: ScanCaller = Depends(scan_caller)):
    return caller.visible(list_scan_history(caller.db))


@router.patch("/api/scans/{scan_id}", response_model=Scan, responses={404: {"model": ReviewError}})
def api_rename_scan(
    scan_id: str, update: ScanNameUpdate, caller: ScanCaller = Depends(scan_caller)
):
    seen = caller.act(SCAN_RENAME, scan_id)
    scan = rename_scan(caller.db, scan_id, update.name)
    if scan is None:
        raise root._review_http_error("scan_not_found", "Scan not found", 404)
    return caller.present(scan, seen)


def _visible_references(
    caller: ScanCaller, seen: SeenScan, references: ScanReferences
) -> tuple[ScanReferences, bool]:
    """The references this caller may view, and whether any were left out."""

    facts = scan_reference_facts(caller.db, seen.facts.scan_id)
    media = dict(
        zip(facts, caller.decide_media([item.media for item in facts.values()]), strict=True)
    )

    def viewable(review_id: str) -> bool:
        target = facts.get(review_id)
        if target is None:
            return False
        account = (
            AccountRef(UserId(target.owner_id), Role(target.owner_role))
            if target.owner_id is not None and target.owner_role is not None
            else None
        )
        return caller.allowed(REVIEW_VIEW, ResourceRef(account, media[review_id], True))

    reviews = [review for review in references.reviews if viewable(review.id)]
    hidden = len(reviews) < len(references.reviews)
    return references.model_copy(
        update={"reviews": reviews, "has_hidden_references": hidden}
    ), hidden


@router.get(
    "/api/scans/{scan_id}/references",
    response_model=ScanReferences,
    responses={404: {"model": ReviewError}},
)
def api_get_scan_references(scan_id: str, caller: ScanCaller = Depends(scan_caller)):
    """List the references this caller may view; deletion still counts every one."""

    seen = caller.act(SCAN_DELETE, scan_id)
    references = get_scan_references(caller.db, scan_id)
    if references is None:
        raise root._review_http_error("scan_not_found", "Scan not found", 404)
    return _visible_references(caller, seen, references)[0]


def _referenced_message(references: ScanReferences, hidden: bool) -> str:
    if references.community_in_progress:
        return "A community operation for this scan is still in progress"
    parts = [f"{review.name} ({review.updated_at.isoformat()})" for review in references.reviews]
    if hidden:
        parts.append("reviews you cannot view")
    formats = ", ".join(value.value.upper() for value in references.active_exports)
    if not parts:
        return f"Delete the active {formats} export before deleting its scan"
    message = "Scan is referenced by saved reviews: " + ", ".join(parts)
    if formats:
        message += f"; delete the active {formats} export before deleting its scan"
    return message


@router.delete(
    "/api/scans/{scan_id}",
    responses={404: {"model": ReviewError}, 409: {"model": ReviewError}},
)
def api_delete_scan(scan_id: str, caller: ScanCaller = Depends(scan_caller)):
    """Delete a scan only after checking every review and export reference."""
    seen = caller.act(SCAN_DELETE, scan_id)
    try:
        deleted = delete_scan(caller.db, scan_id)
    except ScanReferencedError as exc:
        references, hidden = _visible_references(caller, seen, exc.references)
        raise root._review_http_error(
            "scan_referenced",
            _referenced_message(references, hidden),
            references=references,
        ) from exc
    except ValueError as exc:
        raise root._review_http_error("scan_delete_failed", str(exc)) from exc
    if not deleted:
        raise root._review_http_error("scan_not_found", "Scan not found", 404)
    return {"deleted": True}


@router.get("/api/scans/{scan_id}/findings", response_model=ScanFindings)
def api_get_scan_findings(scan_id: str, caller: ScanCaller = Depends(scan_caller)):
    seen = caller.scan(scan_id)
    caller.require(C.SCANS_FINDINGS_READ, MediaRef(seen.media, existence_visible=True))
    findings = get_scan_findings(caller.db, scan_id)
    if not findings:
        raise HTTPException(404, "Scan not found")
    return findings
