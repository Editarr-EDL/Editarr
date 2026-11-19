"""Check automated versioning against real, isolated Git histories."""

import json
import tomllib
from pathlib import Path

import pytest

from scripts.prepare_main_release import (
    BASE_TAG,
    git,
    prepare_release,
    release_notes,
    release_plan,
)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "--quiet", "--initial-branch=main")
    git(root, "config", "user.name", "Release tests")
    git(root, "config", "user.email", "release@example.invalid")
    for name, contents in {
        "pyproject.toml": '[project]\nname = "editarr"\nversion = "0.1.3"\n',
        "scanner/__init__.py": '__version__ = "0.1.3"\n',
        "web/package.json": '{\n  "version": "0.1.3",\n  "name": "editarr-web"\n}\n',
        "website/public/docs/openapi.json": (
            '{\n  "openapi": "3.1.0",\n  "info": {\n    "title": "Editarr",\n'
            '    "version": "0.1.3"\n  },\n  "paths": {}\n}\n'
        ),
        "uv.lock": (
            "version = 1\n"
            '[[package]]\nname = "dependency"\nversion = "0.1.3"\n'
            '[[package]]\nname = "editarr"\nversion = "0.1.3"\nsource = { editable = "." }\n'
        ),
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "Release baseline")
    git(root, "tag", BASE_TAG)
    return root


def commit(root: Path, subject: str) -> str:
    git(root, "commit", "--quiet", "--allow-empty", "-m", subject)
    return git(root, "rev-parse", "HEAD")


def test_each_main_commit_in_one_push_gets_its_own_stable_version(repository):
    first = commit(repository, "First")
    second = commit(repository, "Second")
    assert release_plan(repository, second, BASE_TAG) == [
        {"revision": first, "version": "0.1.4"},
        {"revision": second, "version": "0.1.5"},
    ]
    assert release_plan(repository, second, first) == [{"revision": second, "version": "0.1.5"}]
    assert release_plan(repository, first, None) == [{"revision": first, "version": "0.1.4"}]
    assert release_plan(repository, BASE_TAG, None) == []
    assert release_plan(repository, second, "0" * 40) == release_plan(repository, second, BASE_TAG)


def test_merging_a_topic_creates_one_release_with_notes_for_its_changes(repository):
    git(repository, "switch", "--quiet", "-c", "topic")
    topic = commit(repository, "Improve scan timing")
    git(repository, "switch", "--quiet", "main")
    main = commit(repository, "Main change")
    git(repository, "merge", "--quiet", "--no-ff", "-m", "Merge scan timing", "topic")
    merge = git(repository, "rev-parse", "HEAD")
    assert release_plan(repository, merge, main) == [{"revision": merge, "version": "0.1.5"}]
    notes = release_notes(repository, merge, "Editarr-EDL/Editarr")
    assert "Merge scan timing" in notes
    assert "Improve scan timing" in notes
    assert f"/commit/{topic}" in notes
    assert "Main change" not in notes
    assert "codesign --verify" in notes
    assert "Windows x64 installer and Linux x86_64 AppImage are alpha builds" in notes
    assert "entirely untested in real-world use" in notes


def test_release_commit_preserves_main_and_locked_dependencies(repository, tmp_path):
    revision = commit(repository, "Prepare a release")
    releases = []
    for name in ("build", "publish"):
        checkout = tmp_path / name
        git(repository, "worktree", "add", "--quiet", "--detach", str(checkout), revision)
        release = prepare_release(checkout, revision, "0.1.4")
        releases.append(release)
        assert git(checkout, "rev-parse", "HEAD^") == revision
        assert git(checkout, "status", "--porcelain") == ""
        assert sorted(git(checkout, "diff", "--name-only", revision, release).splitlines()) == [
            "pyproject.toml",
            "scanner/__init__.py",
            "uv.lock",
            "web/package.json",
            "website/public/docs/openapi.json",
        ]
        assert json.loads((checkout / "web/package.json").read_text())["version"] == "0.1.4"
        openapi = json.loads((checkout / "website/public/docs/openapi.json").read_text())
        assert openapi["info"]["version"] == "0.1.4"
        lock = tomllib.loads((checkout / "uv.lock").read_text())["package"]
        assert lock[0]["version"] == "0.1.3"
        assert lock[1]["version"] == "0.1.4"
    assert releases[0] == releases[1]
    assert git(repository, "rev-parse", "main") == revision
    assert git(repository, "status", "--porcelain") == ""


def test_prepare_refuses_the_wrong_version_or_local_work(repository, tmp_path):
    revision = commit(repository, "Main change")
    checkout = tmp_path / "dirty"
    git(repository, "worktree", "add", "--quiet", "--detach", str(checkout), revision)
    with pytest.raises(ValueError, match="commit height"):
        prepare_release(checkout, revision, "0.1.5")
    (checkout / "user-work.txt").write_text("preserve me")
    with pytest.raises(ValueError, match="clean worktree"):
        prepare_release(checkout, revision, "0.1.4")
    assert (checkout / "user-work.txt").read_text() == "preserve me"
