"""The reviewed consent and the reported result of one account disposition.

The preview body is the consent: what the actor saw, naming only reviews, scans,
and jobs they may see and counting the rest. Its digest is the confirmation, and
the stored `impact_json` is exactly the confirmed body. Published-file status
fields that change without changing the deletion (regeneration progress) stay out
of the digest; each file's identity, revision, content digest, and permitted
choices stay in it. Every mode lists the personal publications the actor may see by
the export rules (`require_export`, as MU-36 and MU-37 apply them), which include the
files a transfer moves, and counts the rest.
"""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select

from scanner.db import MediaItemExportRow

from .account_impact import AccountImpact, personal_files
from .export_discovery import AuthorizedPublication

PUBLICATION_IDENTITY = ("id", "publication_id", "publication_revision", "content_sha256")


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _visible(ids, allowed) -> tuple[list[str], int]:
    shown = [value for value in ids if value in allowed]
    return shown, len(ids) - len(shown)


def _publications(caller, impact: AccountImpact) -> dict:
    """The account's personal publications the actor may see (MU-36 export rules); a count of
    the rest."""
    rows = caller.db.scalars(
        select(MediaItemExportRow)
        .where(MediaItemExportRow.id.in_(personal_files(impact.user_id)))
        .order_by(MediaItemExportRow.id)
    ).all()
    shown = [row for row in rows if row.id in impact.visible.exports]
    return {
        "hidden": len(rows) - len(shown),
        "items": [
            AuthorizedPublication.capture(caller, row).publication.model_dump(mode="json")
            for row in shown
        ],
    }


def _consent(impact: AccountImpact, files: list[dict], publications: dict) -> dict:
    visible = impact.visible
    reviews, hidden_reviews = _visible(impact.reviews, visible.reviews)
    scans = [scan for scan in impact.scans if scan.scan_id in visible.scans]
    waiting, hidden_waiting = _visible(impact.waiting_jobs, visible.scans)
    running, hidden_running = _visible(impact.running_jobs, visible.scans)
    transfer = impact.transfer
    return {
        "user_id": impact.user_id,
        "mode": impact.mode.value,
        "destination_id": transfer.destination_id if transfer else None,
        "reviews": {"count": len(impact.reviews), "hidden": hidden_reviews, "ids": reviews},
        "files": files,
        "scans": {
            "hidden": len(impact.scans) - len(scans),
            "items": [
                {"scan_id": scan.scan_id, "status": scan.status, "required": scan.required}
                for scan in scans
            ],
        },
        "jobs": {"waiting": waiting, "running": running, "hidden": hidden_waiting + hidden_running},
        "personal_files": impact.personal_files,
        "publications": publications,
        "transfer": {
            "files_moved": len(transfer.moves),
            "policy_files": len(transfer.policy_exports),
        }
        if transfer
        else None,
        "credentials": {
            "sessions": impact.sessions,
            "api_key": impact.api_key,
            "linked": impact.linked,
        },
        "community": {
            "identity": impact.community_identity,
            "operations": impact.community_operations,
            "pending": impact.community_pending,
        },
        "blockers": [
            {"code": code, "count": amount} for code, amount in sorted(impact.blockers.items())
        ],
    }


def _digest(impact: AccountImpact, body: dict) -> str:
    stable = body | {
        "files": [
            [
                file["review_id"],
                file["publication"]["id"],
                file["publication"]["publication_id"],
                file["publication"]["publication_revision"],
                file["choices"],
            ]
            for file in body["files"]
        ],
        "publications": [
            body["publications"]["hidden"],
            [[item[key] for key in PUBLICATION_IDENTITY] for item in body["publications"]["items"]],
        ],
        "plans": [[plan.review_id, plan.confirmation] for plan in impact.plans],
    }
    return hashlib.sha256(canonical(stable).encode()).hexdigest()


def preview(caller, impact: AccountImpact) -> dict:
    """The consent body with its `confirmation`, projecting files for the `caller`.

    `caller` is the actor's `ReviewAccess`; call under the gate that built `impact`.
    """
    files = [
        {
            "review_id": plan.review_id,
            "publication": AuthorizedPublication.capture(
                caller, caller.db.get(MediaItemExportRow, file.row_id)
            ).publication.model_dump(mode="json"),
            "choices": sorted(file.choices),
        }
        for plan in impact.plans
        for file in plan.files
    ]
    body = _consent(impact, files, _publications(caller, impact))
    return body | {"confirmation": _digest(impact, body)}


@dataclass(frozen=True)
class Outcome:
    """What the disposition changed, by internal ID."""

    reviews_deleted: tuple[str, ...] = ()
    files_retained: tuple[int, ...] = ()
    files_removed: tuple[int, ...] = ()
    scans_deleted: tuple[str, ...] = ()
    scans_kept: tuple[str, ...] = ()
    files_moved: tuple[int, ...] = ()
    policy_files: tuple[int, ...] = ()


def result(
    impact: AccountImpact, outcome: Outcome, credentials: dict, kept: int
) -> tuple[dict, dict]:
    """The actor's result, naming only what they may see, and the full inventory.

    `kept` counts personal publications still targeting or following the account
    after the disposition.
    """
    visible = impact.visible
    mode = impact.mode.value
    reviews, hidden_reviews = _visible(impact.reviews, visible.reviews)
    deleted, hidden_deleted = _visible(outcome.scans_deleted, visible.scans)
    scans_kept, hidden_kept = _visible(outcome.scans_kept, visible.scans)
    jobs = impact.waiting_jobs + impact.running_jobs
    stopped, hidden_jobs = _visible(jobs, visible.scans)
    projected = {
        "user_id": impact.user_id,
        "mode": mode,
        "destination_id": impact.transfer.destination_id if impact.transfer else None,
        "reviews_deleted": reviews if mode == "delete" else [],
        "reviews_retained": reviews if mode == "retain" else [],
        "reviews_transferred": reviews if mode == "transfer" else [],
        "reviews_hidden": hidden_reviews,
        "files_retained": list(outcome.files_retained),
        "files_removed": list(outcome.files_removed),
        "files_moved": len(outcome.files_moved),
        "scans_deleted": deleted,
        "scans_kept": scans_kept,
        "scans_hidden": hidden_deleted + hidden_kept,
        "jobs_stopped": stopped,
        "jobs_hidden": hidden_jobs,
        "personal_files_kept": kept,
        "community_operations_kept": impact.community_operations,
        "community_identity_kept": impact.community_identity,
    } | credentials
    inventory = {
        "reviews": list(impact.reviews),
        "files_retained": list(outcome.files_retained),
        "files_removed": list(outcome.files_removed),
        "files_moved": list(outcome.files_moved),
        "policy_files": list(outcome.policy_files),
        "scans_deleted": list(outcome.scans_deleted),
        "scans_kept": list(outcome.scans_kept),
        "jobs_stopped": list(jobs),
    }
    return projected, inventory
