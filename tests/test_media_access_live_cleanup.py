"""MU-11: the live media access helper removes only Docker resources it proves this run created.

Docker is replaced by an in-memory daemon that answers the helper's commands, so
each case controls the labels and identities a cleanup finds.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[1] / ".github/skills/verify-editarr/scripts/media_access_live_docker.py"
)
RUN = "media-access-0a0b0c0d"


@pytest.fixture(scope="module")
def helper():
    spec = importlib.util.spec_from_file_location("media_access_live_docker_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Daemon:
    """Containers by ID and volumes by name, each with labels and an identity."""

    def __init__(self):
        self.containers: dict[str, dict] = {}
        self.volumes: dict[str, dict] = {}
        self.fail_removal: set[str] = set()
        self.removed: list[str] = []

    def container(self, cid: str, name: str, run: str) -> None:
        self.containers[cid] = {"Id": cid, "Name": f"/{name}", "Config": {"Labels": {LABEL: run}}}

    def volume(self, name: str, created: str, run: str) -> None:
        self.volumes[name] = {"Name": name, "CreatedAt": created, "Labels": {LABEL: run}}

    def _find(self, ref: str) -> dict | None:
        return self.containers.get(ref) or next(
            (c for c in self.containers.values() if c["Name"] == f"/{ref}"), None
        )

    def __call__(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        def done(out: str = "", err: str = "", code: int = 0):
            return subprocess.CompletedProcess(list(args), code, out, err)

        match list(args):
            case ["ps", "-aq", "--no-trunc", "--filter", selector]:
                key, value = selector.removeprefix("label=").split("=", 1)
                ids = [
                    c["Id"]
                    for c in self.containers.values()
                    if c["Config"]["Labels"].get(key) == value
                ]
                return done("\n".join(ids))
            case ["container", "inspect", ref]:
                found = self._find(ref)
                return (
                    done(json.dumps([found]))
                    if found
                    else done(err=f"Error: No such object: {ref}", code=1)
                )
            case ["rm", "-fv", ref]:
                found = self._find(ref)
                if found is None:
                    return done(err=f"Error: No such container: {ref}", code=1)
                if found["Id"] in self.fail_removal:
                    return done(err="Error: removal refused", code=1)
                del self.containers[found["Id"]]
                self.removed.append(found["Id"])
                return done(found["Id"])
            case ["volume", "ls", "-q", "--filter", selector]:
                key, value = selector.removeprefix("label=").split("=", 1)
                return done(
                    "\n".join(n for n, v in self.volumes.items() if v["Labels"].get(key) == value)
                )
            case ["volume", "ls", "-q"]:
                return done("\n".join(self.volumes))
            case ["volume", "inspect", name]:
                found = self.volumes.get(name)
                return (
                    done(json.dumps([found]))
                    if found
                    else done(err=f"Error: no such volume: {name}", code=1)
                )
            case ["volume", "rm", name]:
                if name not in self.volumes:
                    return done(err=f"Error: no such volume: {name}", code=1)
                del self.volumes[name]
                self.removed.append(name)
                return done(name)
        raise AssertionError(f"unexpected docker call {args}")


LABEL = "editarr.verify.run"


@pytest.fixture
def daemon(helper, monkeypatch):
    fake = Daemon()
    monkeypatch.setattr(helper, "docker", fake)
    return fake


@pytest.fixture
def run(tmp_path):
    scratch = tmp_path / f"{RUN}.scratch"
    scratch.mkdir()
    (scratch / ".media-access-scratch").write_text(RUN)
    evidence = tmp_path / RUN
    evidence.mkdir()
    instance = {
        "run": RUN,
        "container": RUN,
        "containerId": "c" * 64,
        "volumes": [f"{RUN}-config", f"{RUN}-cache"],
        "volumeCreatedAt": {
            f"{RUN}-config": "2026-10-22T10:00:00Z",
            f"{RUN}-cache": "2026-10-22T10:00:01Z",
        },
        "volumesBefore": [],
        "scratch": str(scratch),
    }
    return instance, evidence


def _own_everything(daemon, instance):
    daemon.container(instance["containerId"], RUN, RUN)
    for name, created in instance["volumeCreatedAt"].items():
        daemon.volume(name, created, RUN)


def test_cleanup_removes_the_resources_this_run_recorded_and_repeats_cleanly(helper, daemon, run):
    instance, evidence = run
    _own_everything(daemon, instance)
    first = helper.cleanup(instance, evidence)
    assert first["clean"] and not first["kept"] and not first["errors"]
    assert sorted(daemon.removed) == sorted([instance["containerId"], *instance["volumes"]])
    assert not daemon.containers and not daemon.volumes
    again = helper.cleanup(instance, evidence)
    assert again["clean"] and not again["kept"]
    assert json.loads((evidence / "cleanup.json").read_text())["clean"]


def test_cleanup_keeps_recorded_names_that_now_carry_another_runs_label(helper, daemon, run):
    instance, evidence = run
    other = "media-access-ffffffff"
    daemon.container(instance["containerId"], RUN, other)
    for name, created in instance["volumeCreatedAt"].items():
        daemon.volume(name, created, other)
    left = helper.cleanup(instance, evidence)
    assert daemon.removed == []
    assert {k["ref"] for k in left["kept"]} == {instance["containerId"], *instance["volumes"]}
    assert all(k["label"] == other for k in left["kept"])
    assert left["clean"], "nothing labelled for this run remains"


def test_cleanup_keeps_a_recreated_resource_that_reuses_a_recorded_name(helper, daemon, run):
    instance, evidence = run
    daemon.container("d" * 64, RUN, RUN)
    daemon.volume(instance["volumes"][0], "2026-10-23T09:00:00Z", RUN)
    daemon.volume(instance["volumes"][1], instance["volumeCreatedAt"][instance["volumes"][1]], RUN)
    left = helper.cleanup(instance, evidence)
    assert daemon.removed == [instance["volumes"][1]]
    assert {k["ref"] for k in left["kept"]} == {"d" * 64, instance["volumes"][0]}
    assert left["containersLeft"] == ["d" * 64] and left["volumesLeft"] == [instance["volumes"][0]]
    assert not left["clean"]


def test_cleanup_keeps_a_labelled_resource_whose_identity_was_never_recorded(helper, daemon, run):
    instance, evidence = run
    instance |= {"containerId": None, "volumeCreatedAt": {}}
    _own_everything(
        daemon,
        {
            **instance,
            "containerId": "e" * 64,
            "volumeCreatedAt": {n: "t" for n in instance["volumes"]},
        },
    )
    left = helper.cleanup(instance, evidence)
    assert daemon.removed == []
    assert len(left["kept"]) == 3 and not left["clean"]


def test_a_failed_removal_is_reported_and_leaves_the_run_unclean(helper, daemon, run):
    instance, evidence = run
    _own_everything(daemon, instance)
    daemon.fail_removal.add(instance["containerId"])
    left = helper.cleanup(instance, evidence)
    assert left["errors"] and not left["clean"]
    assert left["containersLeft"] == [instance["containerId"]]


def test_the_doctor_refuses_an_unexpected_mount_without_adopting_it(
    helper, daemon, run, monkeypatch
):
    instance, evidence = run
    _own_everything(daemon, instance)
    mounts = [{"Type": "volume", "Name": n} for n in instance["volumes"]]
    mounts.append({"Type": "volume", "Name": "someone-elses-volume"})
    container = daemon.containers[instance["containerId"]]
    container |= {"Mounts": mounts, "State": {"Running": True}, "Image": helper.IMAGE}

    class Reply:
        text = "Healthy"

        @staticmethod
        def json():
            return {"Version": "12.0.0"}

    monkeypatch.setattr(helper.httpx, "get", lambda *a, **k: Reply())
    with pytest.raises(RuntimeError, match="doctor failed"):
        helper.doctor(instance, "http://jellyfin.test", evidence)
    assert "someone-elses-volume" not in instance["volumes"]
    assert json.loads((evidence / "doctor.json").read_text())["unownedVolumes"] == [
        "someone-elses-volume"
    ]
