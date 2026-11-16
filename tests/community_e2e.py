"""Two independent Editarr HTTP processes talking to a real community service."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import httpx

from tests.community_e2e_app import JELLYFIN_ITEM
from tests.jellyfin_setup_fake import ADMIN, ADMIN_PASSWORD, setup_body


class Installation:
    """Own one application's process, private data root and administrator session."""

    def __init__(self, directory, local):
        directory = directory.resolve()
        self.directory = directory
        self.local = local
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("EDITARR_")
        }
        environment.update(
            EDITARR_DATA_DIR=str(directory),
            EDITARR_COMMUNITY_SERVICE_URL="https://community.example",
            EDITARR_COMMUNITY_SERVICE_AUDIENCE="community.example",
            COMMUNITY_E2E_LOCAL=local,
            EDITARR_COMMUNITY_PUBLICATION_REQUEST_BYTES="262144",
            EDITARR_COMMUNITY_PREVIEW_SOURCE_LIMIT="4",
            EDITARR_COMMUNITY_PREVIEW_BOUNDS=json.dumps(
                {
                    "compressed_bytes": 65536,
                    "expanded_bytes": 524288,
                    "events": 100,
                    "text": 4096,
                    "depth": 24,
                }
            ),
            EDITARR_COMMUNITY_DOWNLOAD_BOUNDS=json.dumps(
                {
                    "compressed_bytes": 65536,
                    "expanded_bytes": 524288,
                    "events": 100,
                    "text": 4096,
                    "depth": 24,
                }
            ),
            EDITARR_COMMUNITY_DOWNLOAD_RESPONSE_BYTES="131072",
        )
        self.environment = environment
        self.csrf = None
        self.start()

    def start(self):
        self.client = None
        self.logs = tempfile.TemporaryFile(mode="w+")
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "tests.community_e2e_app"],
                env=self.environment,
                stdout=subprocess.PIPE,
                stderr=self.logs,
                text=True,
            )
        except OSError:
            self.logs.close()
            raise
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                ready = executor.submit(self.process.stdout.readline)
                try:
                    line = ready.result(timeout=20)
                except TimeoutError:
                    self.process.kill()
                    self.process.wait(timeout=10)
                    raise AssertionError("Editarr startup exceeded 20 seconds") from None
            assert line, "Editarr exited before startup"
            port = json.loads(line)["port"]
            self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20)
            self.origin = str(self.client.base_url).rstrip("/")
        except BaseException:
            self.close()
            raise

    def login(self):
        result = self.request(
            "POST",
            "/api/auth/jellyfin/login",
            {"username": ADMIN, "password": ADMIN_PASSWORD, "remember_me": False},
        )
        self.csrf = result["csrf_token"]

    def restart(self):
        self.close()
        self.csrf = None
        self.start()
        self.login()

    def lose_responses(self, path):
        (self.directory / "community-fault.json").write_text(
            json.dumps({"path": path, "remaining": 3})
        )

    def wire(self, path):
        return [
            json.loads(line)
            for line in (self.directory / "community-wire.jsonl").read_text().splitlines()
            if json.loads(line)["path"] == path and json.loads(line)["method"] == "POST"
        ]

    def request(self, method, path, body=None, expected=200):
        headers = {"Origin": self.origin}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        response = self.client.request(method, path, headers=headers, json=body)
        assert response.status_code == expected, (path, response.status_code, response.text)
        return response.json()

    def remote(self, method, path, body=None):
        """Use the real signing client for service operations without a local UI adapter."""
        response = subprocess.run(
            [sys.executable, "-m", "tests.community_e2e_app", "request"],
            env=self.environment,
            input=json.dumps({"method": method, "path": path, "body": body}),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert response.returncode == 0, response.stderr
        return json.loads(response.stdout)

    def enroll(self, control, subject, *, setup=True):
        if setup:
            result = self.request(
                "POST",
                "/api/auth/setup",
                setup_body(),
                expected=201,
            )
            self.csrf = result["csrf_token"]
        started = self.request("POST", "/api/community/enrollment/start")
        response = httpx.post(
            control,
            json={"action": "oauth", "subject": subject, "url": started["authorizationUrl"]},
        )
        assert response.status_code == 200, response.text
        return self.request(
            "POST",
            "/api/community/enrollment/complete",
            {
                "state_id": response.json()["stateId"],
                "confirmed": True,
            },
        )

    def close(self):
        if self.client is not None:
            self.client.close()
        self.process.terminate()
        try:
            self.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate(timeout=10)
        self.logs.seek(0)
        errors = self.logs.read()
        self.logs.close()
        if sys.exc_info()[0] is not None:
            print(errors, file=sys.stderr)


def create_media(path):
    """Generate public-domain test pixels locally instead of using personal media."""
    from scanner.runtime.runtime_support import resolve_ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            resolve_ffmpeg(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-g",
            "30",
            "-c:a",
            "aac",
            "-metadata:s:a:0",
            "language=eng",
            "-metadata:s:a:0",
            "title=Main",
            "-metadata:s:a:1",
            "language=fra",
            "-metadata:s:a:1",
            "title=Unscanned French commentary",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def connect_jellyfin(application):
    """Connect the synthetic Jellyfin server whose movie is the installation's media file."""
    application.request(
        "PUT",
        "/api/settings/jellyfin",
        {"server_url": "http://jellyfin.e2e", "api_key": "synthetic-jellyfin-key"},
    )


TARGET = {"source": "jellyfin", "item_id": JELLYFIN_ITEM}


def find_filters(application):
    """Open Find Filters for the item, as the UI does before showing community results."""
    application.request("POST", "/api/filter-candidates", TARGET)


def donate(author, *, lose_response=False):
    scan = author.request(
        "POST",
        "/api/scans",
        {
            "jellyfin_item_id": JELLYFIN_ITEM,
            "name": "Donor scan",
            "scan_audio": False,
            "scan_video": True,
            "video_model": "nudenet",
        },
        expected=201,
    )
    history = author.request("GET", "/api/scans/history")
    assert next(item for item in history if item["id"] == scan["id"])["status"] == "complete"
    preview = author.request(
        "POST",
        f"/api/community/sharing/sources/scan/{scan['id']}/preview",
        {
            "name": "Public synthetic donation",
            "identity_confirmed": True,
            "media": {"provider": "tmdb", "entity_type": "movie", "reference": "123"},
        },
    )
    approved = author.request(
        "POST",
        f"/api/community/sharing/previews/{preview['previewId']}/approve",
        {"confirmed": True},
    )
    assert approved["state"] == "pending"
    if lose_response:
        author.lose_responses("/v1/contributions")
    published = author.request(
        "POST", f"/api/community/publications/{approved['operationId']}/retry", {"confirmed": True}
    )
    if lose_response:
        assert published["state"] == "paused", published
        attempts = author.wire("/v1/contributions")
        assert len(attempts) == 3 and len({item["digest"] for item in attempts}) == 1
        assert len({item["operation"] for item in attempts}) == 1
        author.restart()
        published = author.request(
            "POST", f"/api/community/publications/{approved['operationId']}/check"
        )
        assert len(author.wire("/v1/contributions")) == 3
    assert published["state"] == "published", published
    return published


def find_and_download(reader):
    lookup = {"target": TARGET}
    find_filters(reader)
    found = reader.request("POST", "/api/community/lookup", lookup)
    assert found["pageError"] is None, found
    (candidate,) = found["page"]["candidates"]
    assert candidate["compatibility"]["selected"] is False
    choice = {name: candidate[name] for name in ("contributionId", "revisionId", "artifactDigest")}
    choice["channels"] = ["video"]
    request = {
        "lookup": lookup | {"target_key": found["targetKey"]},
        "choice": choice,
        "confirmed": True,
    }
    with sqlite3.connect(reader.directory / "scanner.db") as db:
        assert db.execute("SELECT COUNT(*) FROM community_imports").fetchone()[0] == 0
    for field, changed in (("contributionId", "wrong-root"), ("artifactDigest", "a" * 64)):
        refused = reader.request(
            "POST",
            "/api/community/downloads",
            request
            | {
                "choice": choice | {field: changed},
            },
            expected=409,
        )
        assert refused["code"] == "candidate_changed", refused
    reader.lose_responses("/v1/import-receipts")
    downloaded = reader.request("POST", "/api/community/downloads", request)
    assert downloaded["status"] == "imported", downloaded
    assert downloaded["receiptState"] == "submitted", downloaded
    reused = reader.request("POST", "/api/community/downloads", request)
    assert reused["status"] == "reused" and reused["scan"]["id"] == downloaded["scan"]["id"]
    with sqlite3.connect(reader.directory / "scanner.db") as db:
        assert db.execute("SELECT COUNT(*) FROM community_imports").fetchone()[0] == 1
    return downloaded, candidate


def exercise(configuration):
    with tempfile.TemporaryDirectory(prefix="community-e2e-") as directory, ExitStack() as stack:
        driver = Path(directory) / "driver"
        os.environ["EDITARR_DATA_DIR"] = str(driver)
        author = Installation(Path(directory) / "author", configuration["local"])
        stack.callback(author.close)
        reader = Installation(Path(directory) / "reader", configuration["local"])
        stack.callback(reader.close)
        first = author.enroll(configuration["control"], "1554889314549243977")
        second = reader.enroll(configuration["control"], "1554889314549243999")
        assert first["profile"]["contributorId"] != second["profile"]["contributorId"]
        assert author.request("GET", "/api/community/connection")["profile"] == first["profile"]
        assert reader.request("GET", "/api/community/connection")["profile"] == second["profile"]
        donor_media = author.directory / "synthetic.mp4"
        recipient_media = reader.directory / "synthetic.mp4"
        create_media(donor_media)
        shutil.copyfile(donor_media, recipient_media)
        connect_jellyfin(author)
        connect_jellyfin(reader)
        publication = donate(author, lose_response=True)
        imported, candidate = find_and_download(reader)
        assert candidate["attribution"]["contributorId"] == first["profile"]["contributorId"]
        assert imported["scan"]["community_provenance"]["revision_id"] == candidate["revisionId"]
        from tests.community_e2e_backups import backup_roundtrips
        from tests.community_e2e_workflows import (
            authority_and_recovery,
            feedback_and_review,
            selected_audio_and_derivation,
            sibling_and_storage,
        )

        feedback_and_review(
            configuration, author, reader, publication, imported, candidate, recipient_media
        )
        sibling = Installation(Path(directory) / "sibling", configuration["local"])
        stack.callback(sibling.close)
        sibling.enroll(configuration["control"], "1554889314549243999")
        backup_roundtrips(configuration, reader, candidate, recipient_media, Installation)
        authority_and_recovery(configuration, author, reader, candidate, recipient_media)
        sibling_and_storage(configuration, author, reader, sibling, candidate, donor_media)
        selected_audio_and_derivation(
            configuration, author, reader, donor_media, recipient_media, candidate
        )
    print(json.dumps({"instances": 3, "backupRestores": 2}))


if __name__ == "__main__":
    exercise(json.load(sys.stdin))
