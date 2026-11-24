"""Cross-instance votes, authority, recovery and later contributions over a real service."""

import base64
import json
import sqlite3

import httpx

from tests.community_e2e_app import JELLYFIN_ITEM
from tests.community_identity_support import identity_home


def control(configuration, action, **values):
    response = httpx.post(configuration["control"], json={"action": action, **values}, timeout=20)
    assert response.status_code == 200, response.text
    return response.json()


def vote_attempts(application, filter_id):
    return application.wire(f"/v1/filters/{filter_id}/votes")


def feedback(configuration, author, reader, link):
    """Exact submission bytes, an exactly-once vote after response loss, no self-votes,
    a report, and the once-per-day sync."""
    state = control(configuration, "state")
    (remote,) = state["payloads"]
    with sqlite3.connect(author.directory / "scanner.db") as donor:
        original = donor.execute(
            "SELECT artifact_bytes, manifest_bytes, signature_bytes FROM community_publications"
        ).fetchone()
    assert original == tuple(
        base64.b64decode(remote[name]) for name in ("artifact", "manifest", "envelope")
    )
    assert len(state["download_receipts"]) == 1
    filter_id = link["filters"][0]["filterId"]
    reader.lose_responses(f"/v1/filters/{filter_id}/votes")
    lost = reader.master(
        "vote", link_id=link["linkId"], filter_id=filter_id, vote="up", operation_id="vote-e2e-up"
    )
    assert lost["error"] == "network_unavailable", lost
    reader.restart()
    sent = reader.master(
        "send_vote", link_id=link["linkId"], filter_id=filter_id, operation_id="vote-e2e-up"
    )
    assert sent["result"]["vote"] == "up", sent
    attempts = vote_attempts(reader, filter_id)
    assert len(attempts) == 4 and len({item["operation"] for item in attempts}) == 1
    assert len({item["digest"] for item in attempts}) == 1
    view = reader.master("view", link_id=link["linkId"])
    assert view["filters"][0]["myVote"] == "up"
    votes = control(configuration, "state")["filter_votes"]
    assert [(vote["filter_id"], vote["vote"]) for vote in votes] == [(filter_id, "up")]

    from tests.community_e2e import find_and_download

    _found, own = find_and_download(author)
    refused = author.master(
        "vote", link_id=own["linkId"], filter_id=filter_id, vote="up", operation_id="vote-self"
    )
    assert refused == {"error": "vote_ineligible", "details": {"reason": "self_vote"}}, refused

    report = reader.master(
        "report",
        link_id=link["linkId"],
        target_type="filter",
        target_id=filter_id,
        reason="inaccurate",
        note="PRIVATE-E2E-REPORT-NOTE",
    )
    report_id = report["result"]["reportId"]
    control(
        configuration, "moderate", command="resolve_report", resource="report", targetId=report_id
    )
    assert control(configuration, "state")["reports"][0]["state"] == "resolved"

    first = reader.master("sync")
    assert first["ran"] is True and first["state"] == "idle", first
    assert reader.master("sync")["ran"] is False


def authority_and_recovery(configuration, reader, link):
    """Moderation and membership remain authoritative across rotation and recovery."""
    version_id = link["communityVersionId"]
    control(
        configuration, "moderate", command="quarantine", resource="version", targetId=version_id
    )
    gone = reader.master("check", link_id=link["linkId"])
    assert gone["error"] == "version_unavailable", gone
    assert reader.master("view", link_id=link["linkId"])["linkState"] == "unavailable"
    control(configuration, "moderate", command="restore", resource="version", targetId=version_id)
    assert reader.master("check", link_id=link["linkId"])["changed"] is False
    assert reader.master("view", link_id=link["linkId"])["linkState"] == "active"
    control(configuration, "membership", mode="outage")
    assert reader.master("find")["error"] == "membership_unavailable"
    control(configuration, "membership", mode="nonmember")
    assert reader.master("find")["error"] == "membership_ineligible"
    control(configuration, "membership", mode="member")
    old_key = reader.request("GET", "/api/community/connection")["identity"]["keyId"]
    rotated = reader.request("POST", "/api/community/keys/rotate", {"confirmed": True})
    assert rotated["identity"]["keyId"] != old_key
    from tests.community_e2e import Installation

    old_copy = Installation(reader.directory.parent / "restored-full-False", configuration["local"])
    try:
        old_copy.login()
        assert (
            old_copy.request("GET", "/api/community/connection", expected=409)["code"]
            == "key_inactive"
        )
    finally:
        old_copy.close()
    current_key = rotated["identity"]["keyId"]
    private = identity_home(reader.directory).root
    record = json.loads((private / "identity.json").read_text())
    (private / record["key_file"]).unlink()
    started = reader.request(
        "POST", "/api/community/keys/recovery/start", {"old_key_id": current_key, "confirmed": True}
    )
    grant = control(
        configuration, "oauth", subject="1554889314549243999", url=started["authorizationUrl"]
    )
    recovered = reader.request(
        "POST",
        "/api/community/keys/recovery/complete",
        {"state_id": grant["stateId"], "confirmed": True},
    )
    assert recovered["completed"] is not None
    assert reader.request("GET", "/api/community/connection")["identity"]["keyId"] != current_key
    assert reader.master("view", link_id=link["linkId"])["filters"][0]["myVote"] == "up"
    assert len(reader.master("find")["results"]) == 1


def sibling_and_storage(configuration, author, reader, sibling, link, donor_path):
    """Sibling keys vote as one contributor; failed storage never leaves a partial submission."""
    import shutil

    from tests.community_e2e import approve, connect_jellyfin, find_and_download, preview, scan
    from tests.community_e2e import send as send_job

    sibling_profile = sibling.request("GET", "/api/community/connection")
    reader_profile = reader.request("GET", "/api/community/connection")
    contributor = reader_profile["profile"]["contributorId"]
    assert sibling_profile["profile"]["contributorId"] == contributor
    assert sibling_profile["identity"]["keyId"] != reader_profile["identity"]["keyId"]
    shutil.copyfile(donor_path, sibling.directory / "synthetic.mp4")
    connect_jellyfin(sibling)
    _found, copy = find_and_download(sibling)
    filter_id = link["filters"][0]["filterId"]
    voted = sibling.master(
        "vote", link_id=copy["linkId"], filter_id=filter_id, vote="up", operation_id="vote-sibling"
    )
    assert voted["result"]["vote"] == "up", voted
    assert len(control(configuration, "state")["filter_votes"]) == 1

    def upvotes():
        filters = control(configuration, "state")["master_filters"]
        return next(item["upvotes"] for item in filters if item["filter_id"] == filter_id)

    assert upvotes() == 1
    vote = {"resource": "vote", "targetId": filter_id, "contributorId": contributor}
    control(configuration, "moderate", command="exclude_vote", **vote)
    assert upvotes() == 0
    control(configuration, "moderate", command="restore_vote", **vote)
    assert upvotes() == 1
    control(
        configuration, "moderate", command="suspend", resource="contributor", targetId=contributor
    )
    assert (
        sibling.request("GET", "/api/community/connection", expected=409)["code"] == "key_inactive"
    )
    control(
        configuration, "moderate", command="unsuspend", resource="contributor", targetId=contributor
    )
    reader.remote(
        "POST",
        "/v1/keys/revoke",
        {
            "version": 1,
            "installationId": sibling_profile["profile"]["installationId"],
            "keyId": sibling_profile["identity"]["keyId"],
        },
    )
    assert (
        sibling.request("GET", "/api/community/connection", expected=409)["code"] == "key_inactive"
    )
    before = control(configuration, "state")["payloads"]
    control(configuration, "storage-failure", enabled=True)
    try:
        created = scan(author, "Storage rollback scan")
        job = approve(author, preview(author, "scan", created["id"]))
        failed = send_job(author, job)
        assert failed["state"] == "paused", failed
        assert control(configuration, "state")["payloads"] == before
    finally:
        control(configuration, "storage-failure", enabled=False)
    published = send_job(author, job)
    assert published["state"] == "published", published


def contributions_and_updates(configuration, author, reader, link):
    """Only analyzed audio is shared; a linked share confirms the master filter, and the
    follower's check stores the newer revision for review without applying it."""
    from urllib.parse import urlencode

    from tests.community_e2e import approve, preview, scan
    from tests.community_e2e import send as send_job

    item = {"item_id": JELLYFIN_ITEM}
    inventory = author.request("GET", "/api/media/audio-tracks?" + urlencode(item))
    assert len(inventory["tracks"]) == 2
    selected = {"file_version": inventory["file_version"], "track": inventory["tracks"][0]}
    author.request("POST", "/api/media/audio-tracks/selection", item | {"selection": selected})
    audio = scan(
        author,
        "Selected main audio",
        scan_audio=True,
        scan_video=False,
        audio_model="whisper",
        audio_selection=selected,
    )
    prepared = preview(author, "scan", audio["id"])
    assert "Unscanned French commentary" not in json.dumps(prepared)
    keys = {key["type"] for key in prepared["preview"]["artifact"]["media"]["matchKeys"]}
    assert "audio_stream" in keys, keys
    assert send_job(author, approve(author, prepared))["state"] == "published"

    own = scan(reader, "Follower scan")
    linked = preview(reader, "scan", own["id"], link_id=link["linkId"])
    supports = [
        entry
        for entry in linked["preview"]["artifact"]["entries"]
        if entry["proposedFilterId"] == link["filters"][0]["filterId"]
    ]
    assert len(supports) == 1 and linked["estimates"]["confirmations"] >= 1, linked
    shared = send_job(reader, approve(reader, linked))
    assert shared["state"] == "published", shared
    assert shared["result"]["disposition"] == "attached" and shared["result"]["tier"] == "2"
    checked = reader.master("check", link_id=link["linkId"])
    assert checked["changed"] is True, checked
    view = reader.master("view", link_id=link["linkId"])
    assert view["latestRevision"] > view["appliedRevision"]
