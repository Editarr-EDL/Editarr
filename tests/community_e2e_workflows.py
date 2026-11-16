"""Cross-instance feedback, authority and recovery scenarios over real application HTTP."""

import base64
import json
import sqlite3
from pathlib import Path

import httpx

from tests.community_e2e_app import JELLYFIN_ITEM
from tests.community_identity_support import identity_home

TARGET = {"source": "jellyfin", "item_id": JELLYFIN_ITEM}


def control(configuration, action, **values):
    response = httpx.post(configuration["control"], json={"action": action, **values}, timeout=20)
    assert response.status_code == 200, response.text
    return response.json()


def lookup(reader):
    return reader.request("POST", "/api/community/lookup", {"target": TARGET})


def feedback_and_review(configuration, author, reader, publication, imported, candidate, path):
    """Prove exact bytes and exactly-once acknowledgments and votes after response loss."""
    revision = candidate["revisionId"]
    state = control(configuration, "state")
    assert len(state["payloads"]) == 1
    remote = state["payloads"][0]
    with (
        sqlite3.connect(author.directory / "scanner.db") as donor,
        sqlite3.connect(reader.directory / "scanner.db") as recipient,
    ):
        original = donor.execute(
            "SELECT artifact_bytes, manifest_bytes, signature_bytes FROM community_publications"
        ).fetchone()
        saved = recipient.execute(
            "SELECT artifact_bytes, manifest_bytes, signature_bytes FROM community_imports"
        ).fetchone()
        assert (
            original
            == saved
            == tuple(
                base64.b64decode(remote[name]) for name in ("artifact", "manifest", "envelope")
            )
        )
        import_id = recipient.execute("SELECT id FROM community_imports").fetchone()[0]
        assert recipient.execute("SELECT COUNT(*) FROM saved_reviews").fetchone()[0] == 0
    assert state["contribution_statistics"][0]["imports"] == 1
    attempts = reader.wire("/v1/import-receipts")
    assert len(attempts) >= 3 and len({item["operation"] for item in attempts}) == 1
    acknowledged = reader.request(
        "POST", f"/api/community/import-receipts/{import_id}/retry", {"confirmed": True}
    )
    assert acknowledged["state"] == "acknowledged", acknowledged
    assert control(configuration, "state")["contribution_statistics"][0]["imports"] == 1
    vote_path = f"/api/community/votes/{revision}"
    assert reader.request("GET", vote_path)["current"]["eligible"] is True
    reader.lose_responses(f"/v1/contributions/{revision}/vote")
    failed = reader.request(
        "POST",
        vote_path,
        {"vote": "like", "operation_id": "vote-e2e-like", "confirmed": True},
        expected=503,
    )
    assert failed["code"] == "network_unavailable", failed
    assert reader.request("GET", vote_path)["pending"]["state"] == "submitted"
    reader.restart()
    reader.request(
        "POST", vote_path + "/retry", {"operation_id": "vote-e2e-like", "confirmed": True}
    )
    current = reader.request("GET", vote_path)
    assert current["current"]["vote"] == "like" and current["totals"]["likes"] == 1
    assert author.request("GET", vote_path, expected=404)["code"] == "not_found"
    attempts = reader.wire(f"/v1/contributions/{revision}/vote")
    assert len(attempts) == 4 and len({item["operation"] for item in attempts}) == 1
    assert len({item["digest"] for item in attempts}) == 1
    assert lookup(reader)["page"]["candidates"][0]["ranking"]["voteCount"] == 1
    review = reader.request(
        "POST",
        f"/api/library-items/{JELLYFIN_ITEM}/reviews",
        {
            "name": "Explicit community review",
            "memberships": [{"scan_id": imported["scan"]["id"], "role": "video"}],
        },
        expected=201,
    )
    assert len(review["filters"]) == 1
    review = reader.request(
        "PUT",
        f"/api/reviews/{review['id']}",
        {
            "revision": review["revision"],
            "name": review["name"],
            "memberships": review["memberships"],
            "filters": review["filters"],
            "mcf_document": review["mcf_document"] | {"material_end_seconds": 3.0},
            "synchronization": review["synchronization"],
        },
    )
    reader.request("POST", f"/api/reviews/{review['id']}/edl", {"revision": review["revision"]})
    reader.request("POST", f"/api/reviews/{review['id']}/mcf", {"revision": review["revision"]})
    with sqlite3.connect(reader.directory / "scanner.db") as recipient:
        exports = recipient.execute(
            "SELECT destination_path, content_bytes FROM media_item_exports WHERE review_id = ?",
            (review["id"],),
        ).fetchall()
    assert len(exports) == 2
    assert {Path(destination).suffix for destination, _content in exports} == {".edl", ".mcf"}
    for destination, content in exports:
        sidecar = Path(destination)
        assert sidecar.parent.parent == path.parent / sidecar.suffix.removeprefix(".")
        assert sidecar.read_bytes() == content
    assert not path.with_suffix(".edl").exists() and not path.with_suffix(".mcf").exists()


def authority_and_recovery(configuration, author, reader, candidate, path):
    """Moderation and membership remain authoritative across caches, rotation and recovery."""
    revision = candidate["revisionId"]
    before = control(configuration, "state")["payloads"]
    control(configuration, "moderate", command="quarantine", targetId=revision)
    assert lookup(reader)["page"]["candidates"] == []
    control(configuration, "moderate", command="restore", targetId=revision)
    assert len(lookup(reader)["page"]["candidates"]) == 1
    control(configuration, "membership", mode="outage")
    unavailable = lookup(reader)
    assert (
        unavailable["page"] is None
        and unavailable["availabilityError"]["code"] == "membership_unavailable"
    ), unavailable
    control(configuration, "membership", mode="nonmember")
    denied = lookup(reader)
    assert (
        denied["page"] is None and denied["availabilityError"]["code"] == "membership_ineligible"
    ), denied
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
    assert reader.request("GET", f"/api/community/votes/{revision}")["current"]["vote"] == "like"
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
    assert control(configuration, "state")["payloads"] == before
    assert len(lookup(reader)["page"]["candidates"]) == 1


def sibling_and_storage(configuration, author, reader, sibling, candidate, donor_path):
    """Sibling keys share contributor votes; failed storage never leaves partial publication."""
    import shutil

    from tests.community_e2e import connect_jellyfin, find_filters

    sibling_profile = sibling.request("GET", "/api/community/connection")
    reader_profile = reader.request("GET", "/api/community/connection")
    assert sibling_profile["profile"]["contributorId"] == reader_profile["profile"]["contributorId"]
    assert sibling_profile["identity"]["keyId"] != reader_profile["identity"]["keyId"]
    revision = candidate["revisionId"]
    vote_path = f"/api/community/votes/{revision}"
    assert sibling.request("GET", vote_path, expected=404)["code"] == "not_found"
    path = sibling.directory / "synthetic.mp4"
    shutil.copyfile(donor_path, path)
    connect_jellyfin(sibling)
    find_filters(sibling)
    found = lookup(sibling)
    choice = {name: candidate[name] for name in ("contributionId", "revisionId", "artifactDigest")}
    sibling.request(
        "POST",
        "/api/community/downloads",
        {
            "lookup": {
                "target": TARGET,
                "target_key": found["targetKey"],
            },
            "choice": choice | {"channels": ["video"]},
            "confirmed": True,
        },
    )
    assert sibling.request("GET", vote_path)["current"]["vote"] == "like"
    state = control(configuration, "state")
    assert len(state["import_receipts"]) == 1
    assert state["contribution_statistics"][0]["imports"] == 1
    contributor = reader_profile["profile"]["contributorId"]
    control(
        configuration, "moderate", command="exclude_vote", targetId=contributor, revisionId=revision
    )
    assert reader.request("GET", f"/api/community/votes/{revision}")["totals"]["likes"] == 0
    control(
        configuration, "moderate", command="restore_vote", targetId=contributor, revisionId=revision
    )
    control(configuration, "moderate", command="suspend", targetId=contributor)
    assert (
        sibling.request("GET", "/api/community/connection", expected=409)["code"] == "key_inactive"
    )
    control(configuration, "moderate", command="unsuspend", targetId=contributor)
    assert sibling.request("GET", f"/api/community/votes/{revision}")["current"]["vote"] == "like"
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
    assert reader.request("GET", f"/api/community/votes/{revision}")["current"]["vote"] == "like"
    before = control(configuration, "state")["payloads"]
    control(configuration, "storage-failure", enabled=True)
    try:
        scan = author.request(
            "POST",
            "/api/scans",
            {
                "jellyfin_item_id": JELLYFIN_ITEM,
                "name": "Storage rollback scan",
                "scan_audio": False,
                "scan_video": True,
                "video_model": "nudenet",
            },
            expected=201,
        )
        preview = author.request(
            "POST",
            f"/api/community/sharing/sources/scan/{scan['id']}/preview",
            {
                "name": "Rollback donation",
                "identity_confirmed": True,
                "media": {"provider": "tmdb", "entity_type": "movie", "reference": "123"},
            },
        )
        job = author.request(
            "POST",
            f"/api/community/sharing/previews/{preview['previewId']}/approve",
            {"confirmed": True},
        )
        failed = author.request(
            "POST", f"/api/community/publications/{job['operationId']}/retry", {"confirmed": True}
        )
        assert failed["state"] == "paused", failed
        assert control(configuration, "state")["payloads"] == before
    finally:
        control(configuration, "storage-failure", enabled=False)
    published = author.request(
        "POST", f"/api/community/publications/{job['operationId']}/retry", {"confirmed": True}
    )
    assert published["state"] == "published"


def selected_audio_and_derivation(
    configuration, author, reader, donor_path, recipient_path, original
):
    """Only analyzed audio is donated; derived reviews retain signed parent attribution."""
    from urllib.parse import urlencode

    item = {"item_id": JELLYFIN_ITEM}
    donor_inventory = author.request("GET", "/api/media/audio-tracks?" + urlencode(item))
    assert len(donor_inventory["tracks"]) == 2
    selected = {
        "file_version": donor_inventory["file_version"],
        "track": donor_inventory["tracks"][0],
    }
    author.request(
        "POST",
        "/api/media/audio-tracks/selection",
        item | {"selection": selected},
    )
    scan = author.request(
        "POST",
        "/api/scans",
        {
            "jellyfin_item_id": JELLYFIN_ITEM,
            "name": "Selected main audio",
            "scan_audio": True,
            "scan_video": False,
            "audio_model": "whisper",
            "audio_selection": selected,
        },
        expected=201,
    )
    preview = author.request(
        "POST",
        f"/api/community/sharing/sources/scan/{scan['id']}/preview",
        {
            "name": "Main audio donation",
            "identity_confirmed": True,
            "media": {"provider": "tmdb", "entity_type": "movie", "reference": "123"},
        },
    )
    assert "Unscanned French commentary" not in json.dumps(preview)
    assert preview["preview"]["artifact"]["audio_association"] == "identified"
    job = author.request(
        "POST",
        f"/api/community/sharing/previews/{preview['previewId']}/approve",
        {"confirmed": True},
    )
    published = author.request(
        "POST", f"/api/community/publications/{job['operationId']}/retry", {"confirmed": True}
    )
    assert published["state"] == "published"
    inventory = reader.request("GET", "/api/media/audio-tracks?" + urlencode(item))
    lookup_input = {
        "target": TARGET,
        "audio_selection": {
            "file_version": inventory["file_version"],
            "track": inventory["tracks"][0],
        },
    }
    found = reader.request("POST", "/api/community/lookup", lookup_input)
    candidate = next(
        item
        for item in found["page"]["candidates"]
        if item["summary"].get("name") == "Main audio donation"
    )
    assert candidate["summary"]["analyzed_audio"] is not None
    choice = {name: candidate[name] for name in ("contributionId", "revisionId", "artifactDigest")}
    imported = reader.request(
        "POST",
        "/api/community/downloads",
        {
            "lookup": lookup_input | {"target_key": found["targetKey"]},
            "choice": choice | {"channels": ["audio"]},
            "confirmed": True,
        },
    )
    assert imported["status"] == "imported" and imported["scan"]["community_approvals"][0][
        "channels"
    ] == ["audio"]
    review = reader.request(
        "POST",
        f"/api/library-items/{JELLYFIN_ITEM}/reviews",
        {
            "name": "Derived audio review",
            "memberships": [{"scan_id": imported["scan"]["id"], "role": "audio"}],
        },
        expected=201,
    )
    derived = reader.request(
        "POST",
        f"/api/community/sharing/sources/review/{review['id']}/preview",
        {
            "name": "Derived community audio",
            "identity_confirmed": True,
            "media": {"provider": "tmdb", "entity_type": "movie", "reference": "123"},
            "audio_source_scan_id": imported["scan"]["id"],
        },
    )
    assert candidate["revisionId"] in json.dumps(derived["preview"]["artifact"])
    approved = reader.request(
        "POST",
        f"/api/community/sharing/previews/{derived['previewId']}/approve",
        {"confirmed": True},
    )
    shared = reader.request(
        "POST", f"/api/community/publications/{approved['operationId']}/retry", {"confirmed": True}
    )
    assert shared["state"] == "published"
    report = reader.remote(
        "POST",
        f"/v1/contributions/{original['revisionId']}/reports",
        {
            "version": 1,
            "reason": "privacy",
            "note": "PRIVATE-E2E-REPORT-NOTE",
        },
    )
    assert "PRIVATE-E2E-REPORT-NOTE" not in json.dumps(lookup(reader))
    control(
        configuration, "moderate", command="resolve_report", targetId=report["result"]["reportId"]
    )
    assert control(configuration, "state")["reports"][0]["state"] == "resolved"
