"""Independent Editarr HTTP processes sharing and following master filter sets on a real
community service."""

import json
import os
import shutil
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
        return self._run("request", {"method": method, "path": path, "body": body})

    def master(self, action, **values):
        """Run one master-set integration action (`tests.community_e2e_masters`)."""
        return self._run("master", {"action": action, **values})

    def _run(self, command, value):
        response = subprocess.run(
            [sys.executable, "-m", "tests.community_e2e_app", command],
            env=self.environment,
            input=json.dumps(value),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert response.returncode == 0, response.stderr
        result = json.loads(response.stdout.strip().splitlines()[-1])
        if isinstance(result, dict) and "error" in result:
            # Kept for failure messages; an expected refusal leaves the trace unused.
            self.last_error_trace = response.stderr[-3000:]
        return result

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


THEATRICAL = {"choice": "alias", "alias_id": "theatrical"}
MEDIA = {"provider": "tmdb", "entity_type": "movie", "reference": "123"}


def scan(application, name, **options):
    body = {
        "jellyfin_item_id": JELLYFIN_ITEM,
        "name": name,
        "scan_audio": False,
        "scan_video": True,
        "video_model": "nudenet",
    }
    created = application.request("POST", "/api/scans", body | options, expected=201)
    history = application.request("GET", "/api/scans/history")
    assert next(item for item in history if item["id"] == created["id"])["status"] == "complete"
    return created


def preview(application, kind, source_id, **extra):
    return application.request(
        "POST",
        f"/api/community/sharing/sources/{kind}/{source_id}/preview",
        {
            "name": "Public synthetic submission",
            "identity_confirmed": True,
            "media": MEDIA,
            "version_choice": THEATRICAL,
        }
        | extra,
    )


def approve(application, prepared):
    approved = application.request(
        "POST",
        f"/api/community/sharing/previews/{prepared['previewId']}/approve",
        {"confirmed": True},
    )
    assert approved["state"] == "pending", approved
    return approved


def send(application, job):
    return application.request(
        "POST", f"/api/community/publications/{job['operationId']}/retry", {"confirmed": True}
    )


def donate(author, *, lose_response=False):
    """Share a scan; after lost responses the same bytes replay to the stored result."""
    created = scan(author, "Donor scan")
    prepared = preview(author, "scan", created["id"])
    assert prepared["estimates"]["newFilters"] == len(prepared["preview"]["artifact"]["entries"])
    job = approve(author, prepared)
    if lose_response:
        author.lose_responses("/v1/submissions")
    published = send(author, job)
    if lose_response:
        assert published["state"] == "paused", published
        attempts = author.wire("/v1/submissions")
        assert len(attempts) == 3 and len({item["digest"] for item in attempts}) == 1
        assert {item["operation"] for item in attempts} == {job["operationId"]}
        author.restart()
        assert (
            author.request("POST", f"/api/community/publications/{job['operationId']}/check")[
                "state"
            ]
            == "paused"
        )
        assert len(author.wire("/v1/submissions")) == 3
        published = send(author, job)
        attempts = author.wire("/v1/submissions")
        assert len(attempts) == 4 and len({item["digest"] for item in attempts}) == 1
    assert published["state"] == "published", published
    assert published["result"]["disposition"] == "created_confirmed", published
    return published


def find_and_download(application):
    """Find the shared version by its file keys and apply its master set directly."""
    find_filters(application)
    found = application.master("find")
    assert "error" not in found, found
    assert {"sha256_sampled", "md5_quick"} <= set(found["keysUsed"]), found
    (result,) = found["results"]
    assert (result["tier"], result["matchPercent"], result["alignmentRequired"]) == (
        "2",
        80,
        False,
    ), result
    link = application.master(
        "download",
        local_version_id=found["localVersionId"],
        version_id=result["communityVersionId"],
        tier=result["tier"],
        match_percent=result["matchPercent"],
    )
    assert "error" not in link, link
    assert (link["tier"], link["alignmentState"], link["linkState"]) == (
        "2",
        "not_required",
        "active",
    )
    assert link["filters"] and all(item["active"] for item in link["filters"]), link
    return found, link


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
        found, link = find_and_download(reader)
        assert found["results"][0]["communityVersionId"] == publication["result"]["versionId"]
        from tests.community_e2e_backups import backup_roundtrips
        from tests.community_e2e_workflows import (
            authority_and_recovery,
            contributions_and_updates,
            feedback,
            sibling_and_storage,
        )

        feedback(configuration, author, reader, link)
        sibling = Installation(Path(directory) / "sibling", configuration["local"])
        stack.callback(sibling.close)
        sibling.enroll(configuration["control"], "1554889314549243999")
        backup_roundtrips(configuration, reader, link, Installation)
        authority_and_recovery(configuration, reader, link)
        sibling_and_storage(configuration, author, reader, sibling, link, donor_media)
        contributions_and_updates(configuration, author, reader, link)
    print(json.dumps({"instances": 3, "backupRestores": 2}))


if __name__ == "__main__":
    exercise(json.load(sys.stdin))
