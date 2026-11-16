"""Local publication state without signing identity or network dependencies."""

import json

REJECTED = {
    "invalid_publication",
    "invalid_artifact",
    "invalid_artifact_signature",
    "invalid_lineage",
    "publication_limit",
    "body_too_large",
    "operation_conflict",
    "revision_conflict",
    "object_conflict",
    "publication_not_available",
    "sharing_permission_required",
    "source_permission_unknown",
    "source_permission_incompatible",
    "source_attribution_mismatch",
}


def publication_status(operation, payload, *, available=True) -> dict:
    """Project authorized saved rows; disconnection holds unsent work without mutating it."""
    error_code = None if payload.error_code == "not_connected" else payload.error_code
    if operation.state == "acknowledged":
        state = "published"
    elif payload.cancel_requested:
        state = "cancel_pending" if operation.state == "submitted" else "cancelled"
    elif error_code in REJECTED:
        state = "rejected"
    elif error_code:
        state = "paused"
    else:
        state = "pending"
    held = not available and state in {"pending", "paused", "cancel_pending"}
    if held and state == "pending":
        state = "paused"
    result = json.loads(payload.result_json) if payload.result_json else None
    return {
        "operationId": operation.operation_id,
        "ownerId": operation.owner_id,
        "actorId": operation.actor_id,
        "contributorId": operation.contributor_id,
        "contributionId": operation.contribution_id,
        "revisionId": operation.revision_id,
        "artifactDigest": operation.digest,
        "state": state,
        "phase": "acknowledged" if result else operation.state,
        "cancelRequested": payload.cancel_requested,
        "errorCode": "not_connected" if held else error_code,
        "result": result,
    }
