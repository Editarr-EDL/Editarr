"""Exercise release publication with a disposable remote and simulated GitHub responses."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.prepare_main_release import git

PUBLISH = Path(__file__).parents[1] / "scripts/publish_main_release.sh"
TAG = "v0.1.4"
ASSETS = (
    f"Editarr-{TAG}-macOS-arm64.dmg",
    f"Editarr-{TAG}-macOS-Notices.tar.gz",
    f"Editarr-{TAG}-macOS-Third-Party-Source.tar.gz",
)
GITHUB_STUB = r"""
import hashlib
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
state_file = Path(os.environ["GITHUB_TEST_STATE"])
with state_file.with_suffix(".log").open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
state = json.loads(state_file.read_text()) if state_file.exists() else None
if args[:2] == ["release", "view"]:
    if state is None:
        sys.exit(1)
    print(json.dumps(state))
    sys.exit(0)
if args[0] == "api":
    print(os.environ["GITHUB_TEST_LATEST"])
    sys.exit(0)
if args[:2] == ["release", "create"]:
    assert "--draft" in args and "--verify-tag" in args
    state = {"isDraft": True, "assets": [], "url": "https://example.invalid/releases/v0.1.4"}
elif args[:2] == ["release", "upload"]:
    assert state["isDraft"]
    state["assets"] = []
    for filename in args[args.index("--clobber") + 1:]:
        file = Path(filename)
        contents = file.read_bytes()
        state["assets"].append({
            "name": file.name, "size": len(contents), "state": "uploaded",
            "digest": "sha256:" + hashlib.sha256(contents).hexdigest(),
        })
    if os.environ.get("GITHUB_TEST_BAD_DIGEST"):
        state["assets"][0]["digest"] = "sha256:" + "0" * 64
elif args[:2] == ["release", "edit"]:
    assert "--draft=false" in args
    state["isDraft"] = False
else:
    raise AssertionError(args)
state_file.write_text(json.dumps(state))
"""


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "remote", "add", "origin", str(remote))
    git(
        repo,
        "-c",
        "user.name=Release test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "--quiet",
        "-m",
        "Versioned release",
    )
    revision = git(repo, "rev-parse", "HEAD")

    distribution = tmp_path / "distribution"
    assets = distribution / "assets"
    assets.mkdir(parents=True)
    sums = []
    for name in ASSETS:
        content = name.encode()
        (assets / name).write_bytes(content)
        sums.append(f"{hashlib.sha256(content).hexdigest()}  {name}\n")
    (assets / "SHA256SUMS.txt").write_text("".join(sums))
    (distribution / "installed-verification.json").write_text(
        json.dumps(
            {
                "version": TAG[1:],
                "source_revision": revision,
                "target": "macos-arm64",
                "failures": [],
                "checks": {"scratch_cleanup": {"removed": True}},
            }
        )
    )
    notes = tmp_path / "notes.md"
    notes.write_text("Generated release notes")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh.py").write_text(GITHUB_STUB)
    stub = bin_dir / "gh"
    stub.write_text('#!/bin/sh\nexec "$GITHUB_TEST_PYTHON" "$0.py" "$@"\n')
    stub.chmod(0o755)
    state = tmp_path / "github.json"
    monkeypatch.setenv("GITHUB_TEST_PYTHON", sys.executable)
    monkeypatch.setenv("GITHUB_TEST_STATE", str(state))
    monkeypatch.setenv("GITHUB_TEST_LATEST", "v0.1.3")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Editarr-EDL/Editarr")
    monkeypatch.setenv("PATH", str(bin_dir), prepend=":")

    def publish():
        return subprocess.run(
            ["bash", str(PUBLISH), TAG[1:], revision, str(distribution), str(notes)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )

    return publish, repo, revision, state


def test_publish_verifies_all_four_assets_and_is_safe_to_retry(publisher):
    publish, repo, revision, state = publisher
    result = publish()
    assert result.returncode == 0, result.stderr
    draft = json.loads(state.read_text())
    assert draft["isDraft"] is False
    assert len(draft["assets"]) == 4
    assert git(repo, "ls-remote", "origin", f"refs/tags/{TAG}").split()[0] == revision
    assert "--latest=true" in state.with_suffix(".log").read_text()

    state.with_suffix(".log").unlink()
    result = publish()
    assert result.returncode == 0, result.stderr
    log = [json.loads(line)[:2] for line in state.with_suffix(".log").read_text().splitlines()]
    assert log == [["release", "view"]]


def test_wrong_github_digest_leaves_the_release_as_a_draft(publisher, monkeypatch):
    publish, _, _, state = publisher
    monkeypatch.setenv("GITHUB_TEST_BAD_DIGEST", "1")
    assert publish().returncode != 0
    assert json.loads(state.read_text())["isDraft"] is True
    monkeypatch.delenv("GITHUB_TEST_BAD_DIGEST")
    monkeypatch.setenv("GITHUB_TEST_LATEST", "v0.1.10")
    result = publish()
    assert result.returncode == 0, result.stderr
    assert "--latest=false" in state.with_suffix(".log").read_text()
