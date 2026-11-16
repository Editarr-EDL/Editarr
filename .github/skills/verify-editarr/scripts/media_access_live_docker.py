"""Docker ownership for media_access_live.py: run labels, recorded identities, and cleanup.

A run labels each container and volume it creates `editarr.verify.run=<run>` and
records the container ID and each volume's creation time as soon as they exist.
Cleanup removes a resource only while both still match; anything else is kept
and reported, so a stale evidence directory can never remove a newer resource.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import httpx

DOCKER = shutil.which("docker") or "/usr/local/bin/docker"
IMAGE = "jellyfin/jellyfin@sha256:baba630419915985442f315f08b0cf46d9f4c8a0cc4bd38e94a6d35751dd5ef5"
LABEL = "editarr.verify.run"
MARKER = ".media-access-scratch"


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([DOCKER, *args], capture_output=True, text=True, check=check)


def save(evidence: Path, name: str, value: object) -> None:
    (evidence / name).write_text(json.dumps(value, indent=1, default=str) + "\n")


def identity(kind: str, ref: str) -> tuple[str | None, str | None] | None:
    """A container's or volume's run label and immutable identity; None when it is gone.

    A container's identity is its ID. Docker gives a volume none, so its creation
    time stands in: a volume made again under the same name has a new one.
    """

    done = docker(kind, "inspect", ref, check=False)
    if done.returncode:
        if "no such" in done.stderr.lower():
            return None
        raise RuntimeError(f"{kind} inspect {ref}: {done.stderr.strip()}")
    found = json.loads(done.stdout)[0]
    if kind == "container":
        return (found["Config"].get("Labels") or {}).get(LABEL), found["Id"]
    return (found.get("Labels") or {}).get(LABEL), found.get("CreatedAt")


def doctor(instance: dict, base: str, evidence: Path) -> None:
    """Check the server answers as pinned and mounts only this run's volumes.

    An unexpected mount fails the doctor and is reported, never adopted for removal.
    """

    state = json.loads(docker("container", "inspect", instance["containerId"]).stdout)[0]
    unowned = [
        mount.get("Name")
        for mount in state["Mounts"]
        if mount["Type"] == "volume" and mount.get("Name") not in instance["volumes"]
    ]
    report = {
        "health": httpx.get(base + "/health", timeout=5).text.strip(),
        "version": httpx.get(base + "/System/Info/Public", timeout=5).json().get("Version"),
        "running": state["State"]["Running"],
        "label": state["Config"]["Labels"].get(LABEL),
        "image": state["Image"],
        "unownedVolumes": unowned,
    }
    report["ok"] = report == {
        **report,
        "health": "Healthy",
        "version": "12.0.0",
        "running": True,
        "label": instance["run"],
        "unownedVolumes": [],
    }
    save(evidence, "doctor.json", report)
    if not report["ok"]:
        raise RuntimeError(f"doctor failed: {report}")


def _labelled(kind: str, run: str) -> list[str]:
    selector = f"label={LABEL}={run}"
    if kind == "container":
        return docker("ps", "-aq", "--no-trunc", "--filter", selector, check=False).stdout.split()
    return docker("volume", "ls", "-q", "--filter", selector, check=False).stdout.split()


def _remove_owned(instance: dict, kind: str, kept: list[dict], errors: list[str]) -> None:
    """Remove each recorded or labelled resource of `kind` whose label and identity match."""

    run = instance["run"]
    if kind == "container":
        expected = {
            instance.get("containerId") or instance["container"]: instance.get("containerId")
        }
    else:
        made_at = instance.get("volumeCreatedAt") or {}
        expected = {name: made_at.get(name) for name in instance["volumes"]}
    seen: set[str] = set()
    for ref in dict.fromkeys([*expected, *_labelled(kind, run)]):
        try:
            found = identity(kind, ref)
        except RuntimeError as error:
            errors.append(str(error))
            continue
        if found is None:
            continue
        label, made = found
        key = made if kind == "container" else ref
        if key in seen:
            continue
        seen.add(key)
        wanted = instance.get("containerId") if kind == "container" else expected.get(ref)
        if label != run or made is None or made != wanted:
            kept.append({"kind": kind, "ref": ref, "label": label, "identity": made})
            continue
        command = ("rm", "-fv", made) if kind == "container" else ("volume", "rm", ref)
        done = docker(*command, check=False)
        if done.returncode:
            errors.append(f"{kind} rm {ref}: {done.stderr.strip()}")


def cleanup(instance: dict, evidence: Path) -> dict:
    """Remove the run's proven container, volumes, and scratch; report what was kept."""

    errors: list[str] = []
    kept: list[dict] = []
    for kind in ("container", "volume"):
        _remove_owned(instance, kind, kept, errors)
    scratch = Path(instance["scratch"])
    if scratch.exists():
        if (scratch / MARKER).read_text().strip() == instance["run"]:
            shutil.rmtree(scratch, ignore_errors=True)
        else:
            errors.append("scratch marker does not name this run; kept")
    left = {
        "containersLeft": _labelled("container", instance["run"]),
        "volumesLeft": _labelled("volume", instance["run"]),
        "kept": kept,
        "newUnlabelledVolumes": sorted(
            set(docker("volume", "ls", "-q", check=False).stdout.split())
            - set(instance["volumesBefore"])
        ),
        "scratchRemoved": not scratch.exists(),
        "evidenceRetained": evidence.exists(),
        "errors": errors,
    }
    left["clean"] = (
        not (left["containersLeft"] or left["volumesLeft"] or errors) and left["scratchRemoved"]
    )
    save(evidence, "cleanup.json", left)
    return left
