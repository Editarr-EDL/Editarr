"""Drive the master-set client integration inside one journey installation.

The local master-set routes are added separately, so the journey calls the same
integration the routes will call, against the installation's real database, identity
and the loopback service. Run as `python -m tests.community_e2e_app master` with a
JSON action on stdin; prints a JSON result or `{"error": code, "details": {...}}`.
"""

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

from sqlalchemy import select

from scanner.db import CommunityMasterFilterRow, MediaItemRow, UserRow, get_session_factory
from scanner.integrations.community import find_targets
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.find import find_versions
from scanner.integrations.community.find_targets import FindTarget, capture_find_keys
from scanner.integrations.community.master_service import MasterService
from scanner.integrations.community.master_sync import MasterSync
from scanner.integrations.community.master_votes import MasterFeedback
from scanner.integrations.community.masters import MasterSets
from scanner.integrations.community.transport import CommunityFailure, ServiceEndpoint
from scanner.persistence.repository.community_master_filters import master_view
from scanner.persistence.repository.media_identity_store import observe_sampled_version
from scanner.persistence.repository.repository_community import user_identity_home
from tests.community_e2e_app import JELLYFIN_ITEM, SyntheticJellyfin, transport

TARGET = FindTarget(source="jellyfin", item_id=JELLYFIN_ITEM)


def _client(db):
    owner = db.scalar(select(UserRow.id).where(UserRow.role == "owner"))
    home = user_identity_home(db, Path(os.environ["EDITARR_DATA_DIR"]), owner)
    endpoint = ServiceEndpoint("https://community.example", "community.example")
    return CommunityClient(db, home, transport(endpoint)), owner


def _local_version(db, captured) -> str:
    media = db.scalar(select(MediaItemRow).where(MediaItemRow.jellyfin_id == JELLYFIN_ITEM))
    version = observe_sampled_version(
        db,
        media=media,
        media_source_id=None,
        source_path=str(captured.path),
        fingerprint=captured.sampled,
    )
    db.commit()
    return version


def _view(db, owner, link_id) -> dict:
    view = master_view(db, owner, link_id)
    link = view.link
    result = {
        "linkId": link.id,
        "communityVersionId": link.community_version_id,
        "tier": link.tier,
        "alignmentState": link.alignment_state,
        "linkState": link.link_state,
        "appliedRevision": link.applied_revision,
        "latestRevision": link.latest_revision,
        "pendingUpdate": view.pending_update,
        "filters": [
            {
                "filterId": item.filter_id,
                "state": item.state,
                "myVote": item.my_vote,
                "active": item.active,
                "locallyVerified": item.locally_verified,
                "contributorCount": item.master.get("contributorCount"),
            }
            for item in view.filters
        ],
    }
    db.rollback()
    return result


async def _act(db, action: dict):
    client, owner = _client(db)
    kind = action["action"]
    if kind == "find":
        captured = capture_find_keys(db, TARGET)
        found = await find_versions(MasterService(client), captured.keys)
        return {
            "localVersionId": _local_version(db, captured),
            "keysUsed": [key.type for key in captured.keys.file_keys],
            "results": [item.as_json() for item in found],
        }
    masters = MasterSets(client, owner)
    if kind == "download":
        link = await masters.download(
            media_version_id=action["local_version_id"],
            community_version_id=action["version_id"],
            tier=action["tier"],
            match_percent=action.get("match_percent"),
            replace=action.get("replace", False),
        )
        return _view(db, owner, link.id)
    if kind == "view":
        return _view(db, owner, action["link_id"])
    if kind == "check":
        return await masters.check(action["link_id"])
    if kind == "sync":
        outcome = await MasterSync(client, owner).run()
        status = outcome.status
        return {
            "ran": outcome.ran,
            "state": status.state,
            "unread": status.unread_count,
            "kinds": [note.kind for note in status.notifications],
        }
    feedback = MasterFeedback(client, owner)
    if kind == "vote":
        return await feedback.vote(
            action["link_id"], action["filter_id"], action["vote"], action["operation_id"]
        )
    if kind == "send_vote":
        return await feedback.send_vote(
            action["link_id"], action["filter_id"], action["operation_id"]
        )
    if kind == "report":
        return await feedback.report(
            action["link_id"],
            target_type=action["target_type"],
            target_id=action["target_id"],
            reason=action["reason"],
            note=action.get("note"),
        )
    if kind == "filters":
        return list(
            db.scalars(
                select(CommunityMasterFilterRow.filter_id).where(
                    CommunityMasterFilterRow.link_id == action["link_id"]
                )
            )
        )
    raise ValueError(f"Unknown action {kind!r}")


def run() -> None:
    find_targets.JellyfinClient = SyntheticJellyfin
    action = json.load(sys.stdin)
    with get_session_factory()() as db:
        try:
            result = asyncio.run(_act(db, action))
        except CommunityFailure as error:
            traceback.print_exc()
            result = {"error": error.code, "details": error.details}
        except Exception as error:  # noqa: BLE001 - reported to the journey driver
            code = getattr(error, "code", None)
            if code is None:
                raise
            result = {"error": code, "details": {}}
    print(json.dumps(result))
