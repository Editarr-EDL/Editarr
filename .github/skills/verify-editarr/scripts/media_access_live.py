"""Live Jellyfin 12.0.0 proof of media access through Editarr's real adapter.

Run from a checkout with `PYTHONPATH` set to it and scratch `EDITARR_DATA_DIR`
and `EDITARR_LOG_DIR`:

  media_access_live.py run [--record] [--inject-failure]
  media_access_live.py cleanup EVIDENCE_DIRECTORY

`run` launches a disposable pinned Jellyfin container over synthetic media,
checks it, drives `JellyfinUserAdapter` and `decide_media` as the user Casey,
and always cleans up. Evidence goes to
web/test-results/verify-editarr/media-access-<run>/. `--record` rewrites the
catalog replay fixture. `--inject-failure` fails right after the doctor so the
cleanup can be rehearsed. `cleanup` removes a run's container and volumes again
and is safe to repeat. It removes a resource only while it carries this run's
label and the container ID or volume creation time recorded when it was made;
anything else is kept and reported under `kept`. Exit codes: 0 pass,
1 failure, 2 blocked, 64 usage. Passwords, tokens, and keys stay in memory.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

CHECKOUT = Path(__file__).resolve().parents[4]
import scanner  # noqa: E402

assert scanner.__file__.startswith(f"{CHECKOUT}/"), scanner.__file__

from scanner.integrations.jellyfin_users import JellyfinUserAdapter  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media_access_live_drive as drive  # noqa: E402
from media_access_live_docker import (  # noqa: E402
    IMAGE,
    LABEL,
    MARKER,
    cleanup,
    docker,
    doctor,
    identity,
    save,
)

FFMPEG = "/usr/lib/jellyfin-ffmpeg/ffmpeg"
ROOT = CHECKOUT / "web/test-results/verify-editarr"
FIXTURE = CHECKOUT / "tests/fixtures/multi-user/jellyfin-catalog-v3/recording.json"
CLIENT = 'MediaBrowser Client="editarr-verify", Device="verify", DeviceId="verify", Version="1"'


class Blocked(Exception):
    """A prerequisite is missing; nothing was launched."""


class Http:
    """An httpx client that reports a failed call by method, path, and status only."""

    def __init__(self, base: str, authorization: str):
        self.client = httpx.Client(base_url=base, timeout=60)
        self.authorization = authorization

    def call(self, method: str, path: str, **kwargs) -> httpx.Response:
        reply = self.client.request(
            method, path, headers={"Authorization": self.authorization}, **kwargs
        )
        if reply.status_code >= 400:
            raise RuntimeError(f"{method} {path.split('?')[0]} -> {reply.status_code}")
        return reply


def launch(instance: dict, evidence: Path) -> str:
    """Create the run's labelled volumes, synthetic media, and server; return its URL.

    Each resource's identity is saved as soon as it exists, so cleanup can prove it.
    """

    media, music, config = (Path(instance["scratch"]) / n for n in ("media", "music", "config"))
    media.mkdir(parents=True)
    music.mkdir()
    config.mkdir()
    owned = ["--label", f"{LABEL}={instance['run']}"]
    for volume in instance["volumes"]:
        if identity("volume", volume) is not None:
            raise RuntimeError(f"volume {volume} already exists")
        docker("volume", "create", *owned, volume)
        instance["volumeCreatedAt"][volume] = identity("volume", volume)[1]
        save(evidence, "instance.json", instance)
    mounts = ["-v", f"{config}:/config", "-v", f"{instance['volumes'][0]}:/cache"]
    docker(
        "run", "--rm", *owned, *mounts, "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{media}:/media", "--entrypoint", FFMPEG, IMAGE,
        "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=160x120", "-f", "lavfi",
        "-i", "sine=d=1", "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac",
        "/media/plain.mkv", "-map", "1:a", "-c:a", "aac", "/media/plain.m4a",
        "-map", "0:v", "-frames:v", "1", "/media/plain.jpg",
    )  # fmt: skip
    drive.write_media(media)
    instance["containerId"] = docker(
        "run", "-d", "--name", instance["container"], *owned, *mounts,
        "-p", f"127.0.0.1:{instance.get('port', '')}:8096",
        "-v", f"{media}:/media:ro", "-v", f"{music}:/music:ro", IMAGE,
    ).stdout.strip()  # fmt: skip
    save(evidence, "instance.json", instance)
    port = docker("port", instance["containerId"], "8096").stdout.split()[0].rsplit(":", 1)[1]
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            if httpx.get(base + "/health", timeout=5).text.strip() == "Healthy":
                return base
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise TimeoutError("Jellyfin did not become healthy")


def setup(base: str) -> tuple[Http, str, str, list[str]]:
    """Complete the startup wizard, add the libraries, and open only Family and Home to Casey."""

    admin = Http(base, CLIENT)
    password, casey_password = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
    admin.call(
        "POST",
        "/Startup/Configuration",
        json={"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"},
    )
    admin.call("GET", "/Startup/User")
    admin.call("POST", "/Startup/User", json={"Name": "root", "Password": password})
    admin.call(
        "POST",
        "/Startup/RemoteAccess",
        json={"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False},
    )
    admin.call("POST", "/Startup/Complete")
    signed = admin.call(
        "POST", "/Users/AuthenticateByName", json={"Username": "root", "Pw": password}
    )
    token = signed.json()["AccessToken"]
    admin.authorization = f'{CLIENT}, Token="{token}"'
    admin.call("POST", "/Auth/Keys?app=editarr-verify")
    keys = admin.call("GET", "/Auth/Keys").json()["Items"]
    key = next(k["AccessToken"] for k in keys if k.get("AppName") == "editarr-verify")
    casey = admin.call(
        "POST", "/Users/New", json={"Name": "casey", "Password": casey_password}
    ).json()["Id"]
    for name, kind, types in drive.LIBRARIES:
        add_library(admin, name, kind, types, f"/media/{name}")
    folders = {f["Name"]: f["ItemId"] for f in admin.call("GET", "/Library/VirtualFolders").json()}
    policy = admin.call("GET", f"/Users/{casey}").json()["Policy"]
    policy.update(
        {"EnableAllFolders": False, "EnabledFolders": [folders["Family"], folders["Home"]]}
    )
    admin.call("POST", f"/Users/{casey}/Policy", json=policy)
    return admin, key, casey, [password, casey_password, token, key]


def add_library(admin: Http, name: str, kind: str, types: list[str], path: str) -> None:
    options = [
        {
            "Type": t,
            "MetadataFetchers": [],
            "MetadataFetcherOrder": [],
            "ImageFetchers": [],
            "ImageFetcherOrder": [],
        }
        for t in types
    ]
    admin.call(
        "POST",
        f"/Library/VirtualFolders?name={name}&collectionType={kind}&refreshLibrary=false",
        json={"LibraryOptions": {"PathInfos": [{"Path": path}], "EnableRealtimeMonitor": False,
                                 "SaveLocalMetadata": False, "TypeOptions": options}},
    )  # fmt: skip


def wait_indexed(base: str, key: str, admin: Http) -> None:
    """Wait until the adapter's catalog holds every synthetic library file and part."""

    expected = {f"/media/{path}" for path in drive.library_files()}
    deadline, refreshed, found = time.monotonic() + 300, 0.0, set()
    with JellyfinUserAdapter(base, key) as adapter:
        server = adapter.server().server_id
        while time.monotonic() < deadline:
            items = getattr(adapter.catalog(server), "items", ())
            found = {p for i in items for p in (i.path, *(v.path for v in i.versions)) if p}
            if expected <= found:
                return
            if time.monotonic() - refreshed > 20:
                admin.call("POST", "/Library/Refresh")
                refreshed = time.monotonic()
            time.sleep(3)
    raise TimeoutError(f"not listed: {sorted(expected - found)}")


def record(entries: list[dict[str, object]], secrets_used: list[str]) -> str:
    text = json.dumps(entries)
    assert not any(value in text for value in secrets_used), "a secret reached the recording"
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(
            {
                "fixture": "editarr-jellyfin-catalog-3",
                "provenance": {
                    "kind": "recorded",
                    "image": IMAGE,
                    "jellyfinVersion": entries[0]["body"]["Version"],
                    "recordedAt": datetime.now(UTC).isoformat(timespec="seconds"),
                    "media": "synthetic clips, a song, a photo, collections, a merged and a "
                    "stacked movie (media_access_live_drive.py)",
                    "note": "Unmodified server-key responses; library folders, catalog "
                    "listings whole and by three, grouped controls, and stacked parts.",
                },
                "responses": entries,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    return str(FIXTURE.relative_to(CHECKOUT))


def redaction(evidence: Path, secrets_used: list[str]) -> bool:
    files = [path for path in evidence.rglob("*") if path.is_file()]
    hits = sum(
        secret in path.read_text(errors="ignore") for path in files for secret in secrets_used
    )
    save(
        evidence,
        "redaction.json",
        {"filesScanned": len(files), "secretsChecked": len(secrets_used), "matches": hits},
    )
    return hits == 0


def run(record_fixture: bool, inject_failure: bool) -> int:
    try:
        docker("version", "--format", "{{.Server.Version}}")
        docker("image", "inspect", IMAGE)
    except (OSError, subprocess.CalledProcessError) as error:
        raise Blocked(f"Docker and the pinned image are required ({IMAGE}): {error}") from error
    name = "media-access-" + secrets.token_hex(4)
    evidence, scratch = ROOT / name, ROOT / f"{name}.scratch"
    evidence.mkdir(parents=True)
    scratch.mkdir()
    (scratch / MARKER).write_text(name)
    instance = {
        "run": name,
        "image": IMAGE,
        "container": name,
        "containerId": None,
        "volumes": [f"{name}-cache"],
        "volumeCreatedAt": {},
        "volumesBefore": docker("volume", "ls", "-q").stdout.split(),
        "scratch": str(scratch),
        "checkout": str(CHECKOUT),
        "head": subprocess.run(
            ["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip(),
        "startedAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    save(evidence, "instance.json", instance)
    print(f"Evidence: {evidence}", flush=True)
    secrets_used: list[str] = []
    failures: list[str] = []
    try:
        base = launch(instance, evidence)
        doctor(instance, base, evidence)
        if inject_failure:
            raise RuntimeError("injected failure after the doctor passed")
        admin, key, casey, secrets_used = setup(base)
        keyed = Http(base, f'MediaBrowser Token="{key}"')
        admin.call("POST", "/Library/Refresh")
        wait_indexed(base, key, admin)
        results = drive.group(keyed, admin)
        listed, entries = drive.listings(keyed)
        results |= listed
        if record_fixture:
            results["recorded"] = record(entries, secrets_used)
        with JellyfinUserAdapter(base, key) as adapter:
            results |= drive.decisions(
                adapter, admin, casey, Path(instance["scratch"]) / "media", add_library
            )
        results |= drive.paging(base, key, admin, Path(instance["scratch"]) / "media")
        failures = [
            name
            for name, value in results.items()
            if isinstance(value, dict) and value.get("ok") is False
        ]
        save(evidence, "results.json", results)
    except Exception as error:  # noqa: BLE001
        failures.append(f"{type(error).__name__}: {error}")
        save(evidence, "failure.json", {"error": failures[-1]})
    finally:
        left = cleanup(instance, evidence)
        clean_text = redaction(evidence, secrets_used)
    print(json.dumps({"failures": failures, "cleanup": left, "redacted": clean_text}, indent=1))
    return 1 if failures or not left["clean"] or not clean_text else 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["run"] and set(argv[1:]) <= {"--record", "--inject-failure"}:
        try:
            return run("--record" in argv, "--inject-failure" in argv)
        except Blocked as blocked:
            print(f"BLOCKED: {blocked}", file=sys.stderr)
            return 2
    if argv[:1] == ["cleanup"] and len(argv) == 2:
        evidence = Path(argv[1]).resolve()
        left = cleanup(json.loads((evidence / "instance.json").read_text()), evidence)
        print(json.dumps(left, indent=1))
        return 0 if left["clean"] else 1
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
