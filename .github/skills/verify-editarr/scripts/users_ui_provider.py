"""Adapt the existing pinned Jellyfin lifecycle for the Node users and identity journeys.

`launch DIR PORT` starts an empty server. `launch DIR PORT --libraries` also
completes setup with media_access_live's synthetic libraries, opens only Family
and Home to `casey` and only Movies to `dana`, waits until every file is listed,
and reports the administrator `root`'s password and the host directory mounted at
the container's `/media`. `logs DIR`
saves the recorded container's log to DIR/jellyfin.log.
`VERIFY_DOCKER_RUN_PREFIX` names the run label's prefix, so a caller can mark
every container and volume with its own task.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

import media_access_live as live
from media_access_live_docker import IMAGE, MARKER, cleanup, docker, doctor, save


def main(arguments: list[str]) -> int:
    """Launch or clean only the provider recorded in the supplied evidence directory."""
    operation, directory, *options = arguments
    evidence = Path(directory)
    if operation == "cleanup":
        result = cleanup(json.loads((evidence / "instance.json").read_text()), evidence)
        print(json.dumps(result))
        return 0 if result["clean"] else 1
    if operation == "logs":
        instance = json.loads((evidence / "instance.json").read_text())
        log = docker("logs", "--timestamps", instance["container"], check=False)
        (evidence / "jellyfin.log").write_text(log.stdout + log.stderr)
        print(json.dumps({"saved": str(evidence / "jellyfin.log")}))
        return 0
    if operation != "launch" or not options or options[1:] not in ([], ["--libraries"]):
        return 64
    port = int(options[0])
    if not 1024 <= port <= 65535:
        return 64
    evidence.mkdir(mode=0o700)
    prefix = os.environ.get("VERIFY_DOCKER_RUN_PREFIX", "users-ui-jellyfin-")
    scratch = Path(tempfile.mkdtemp(prefix=prefix))
    name = scratch.name
    (scratch / MARKER).write_text(name)
    instance = {
        "run": name,
        "scratch": str(scratch),
        "container": name,
        "containerId": None,
        "volumes": [f"{name}-cache"],
        "volumeCreatedAt": {},
        "volumesBefore": docker("volume", "ls", "-q").stdout.split(),
        "port": port,
        "image": IMAGE,
    }
    save(evidence, "instance.json", instance)
    try:
        base = live.launch(instance, evidence)
        doctor(instance, base, evidence)
        result = {"base": base, "image": IMAGE, "evidence": str(evidence), "run": name}
        if options[1:]:
            result |= _libraries(base) | {"media": str(scratch / "media")}
        print(json.dumps(result))
        return 0
    except BaseException:
        cleanup(instance, evidence)
        raise


def _libraries(base: str) -> dict[str, object]:
    """Add the synthetic libraries and two restricted users; return their secrets."""
    admin, key, casey, used = live.setup(base)
    dana_password = secrets.token_urlsafe(18)
    dana = admin.call("POST", "/Users/New", json={"Name": "dana", "Password": dana_password})
    folders = {f["Name"]: f["ItemId"] for f in admin.call("GET", "/Library/VirtualFolders").json()}
    policy = admin.call("GET", f"/Users/{dana.json()['Id']}").json()["Policy"]
    policy.update({"EnableAllFolders": False, "EnabledFolders": [folders["Movies"]]})
    admin.call("POST", f"/Users/{dana.json()['Id']}/Policy", json=policy)
    live.wait_indexed(base, key, admin)
    return {
        "key": key,
        "admin": {"name": "root", "password": used[0]},
        "users": {
            "casey": {"id": casey, "password": used[1]},
            "dana": {"id": dana.json()["Id"], "password": dana_password},
        },
        "folders": folders,
    }


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
