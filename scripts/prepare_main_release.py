"""Assign patch versions using the first-parent history of Editarr's main branch."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

from scripts.release_assets import validate_release_versions

BASE_TAG = "v0.1.3"
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
VERSION_FILES = (
    "pyproject.toml",
    "scanner/__init__.py",
    "web/package.json",
    "uv.lock",
    "website/public/docs/openapi.json",
)


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=root, env=env, text=True).strip()


def release_plan(root: Path, head: str, before: str | None) -> list[dict[str, str]]:
    """Version each new first-parent revision; omit commits only present on merged branches."""
    base = git(root, "rev-parse", f"{BASE_TAG}^{{commit}}")
    history = git(root, "rev-list", "--first-parent", "--reverse", head).splitlines()
    if base not in history:
        raise ValueError(f"{head} does not descend on the first-parent history of {BASE_TAG}")
    if not before:
        selected = {history[-1]}
    elif before == "0" * 40:
        selected = set(history)
    else:
        selected = set(git(root, "rev-list", "--first-parent", head, f"^{before}").splitlines())

    major, minor, patch = map(int, BASE_TAG.removeprefix("v").split("."))
    return [
        {"revision": revision, "version": f"{major}.{minor}.{patch + distance}"}
        for distance, revision in enumerate(history[history.index(base) + 1 :], start=1)
        if revision in selected
    ]


def _replace_once(text: str, pattern: str, replacement: str, path: Path) -> str:
    result, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Expected one version field to update in {path}, found {count}")
    return result


def version_files(root: Path, version: str) -> dict[Path, str]:
    """Update only Editarr's version fields, keeping the dependency resolution byte-for-byte."""
    previous = re.escape(validate_release_versions(root))
    patterns = (
        rf'^version = "{previous}"$',
        rf'^__version__ = "{previous}"$',
        rf'^([ \t]*"version": )"{previous}"(,?)$',
        rf'(^\[\[package\]\]\nname = "editarr"\n)version = "{previous}"$',
        rf'^([ \t]*"version": )"{previous}"$',
    )
    replacements = (
        f'version = "{version}"',
        f'__version__ = "{version}"',
        rf'\g<1>"{version}"\g<2>',
        rf'\g<1>version = "{version}"',
        rf'\g<1>"{version}"',
    )
    paths = [root / name for name in VERSION_FILES]
    result = {
        path: _replace_once(path.read_text(encoding="utf-8"), pattern, replacement, path)
        for path, pattern, replacement in zip(paths, patterns, replacements, strict=True)
    }
    json.loads(result[root / "web/package.json"])
    json.loads(result[root / "website/public/docs/openapi.json"])
    tomllib.loads(result[root / "uv.lock"])
    return result


def prepare_release(root: Path, revision: str, version: str) -> str:
    """Commit the generated version in a disposable detached worktree."""
    if git(root, "status", "--porcelain") or git(root, "rev-parse", "HEAD") != revision:
        raise ValueError("Release preparation requires a clean worktree at the requested revision")
    branch = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "HEAD"], cwd=root, capture_output=True, check=False
    )
    if branch.returncode != 1:
        raise ValueError("Release preparation requires a detached worktree")
    if release_plan(root, revision, None) != [{"revision": revision, "version": version}]:
        raise ValueError("The requested version does not match the main-branch commit height")
    for path, content in version_files(root, version).items():
        path.write_text(content, encoding="utf-8", newline="\n")
    if validate_release_versions(root) != version:
        raise ValueError("Release version validation failed")
    git(root, "diff", "--check")
    git(root, "add", "--", *VERSION_FILES)
    write_time = git(root, "show", "-s", "--format=%cI", revision)
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME=BOT_NAME,
        GIT_AUTHOR_EMAIL=BOT_EMAIL,
        GIT_AUTHOR_DATE=write_time,
        GIT_COMMITTER_NAME=BOT_NAME,
        GIT_COMMITTER_EMAIL=BOT_EMAIL,
        GIT_COMMITTER_DATE=write_time,
    )
    tree = git(root, "write-tree")
    commit = git(root, "commit-tree", tree, "-p", revision, "-m", f"Editarr v{version}", env=env)
    git(root, "switch", "--quiet", "--detach", commit)
    if git(root, "status", "--porcelain"):
        raise RuntimeError("The generated release worktree is dirty")
    return commit


def release_notes(root: Path, revision: str, repository: str) -> str:
    """Describe the commits introduced by one main-branch revision."""
    history = git(root, "log", "--format=%H%x09%s", f"{revision}^..{revision}")
    lines = ["## Changes", ""]
    for entry in history.splitlines():
        sha, _, subject = entry.partition("\t")
        lines.append(
            f"- {subject or 'Commit'} ([{sha[:7]}](https://github.com/{repository}/commit/{sha}))"
        )
    return "\n".join(
        [
            "## Windows and Linux alpha builds",
            "",
            "**The Windows x64 installer and Linux x86_64 AppImage are alpha builds "
            "and entirely untested in real-world use.** The Windows installer is unsigned. "
            "On Linux, make the AppImage executable with `chmod +x` before launching it.",
            "",
            *lines,
            "",
            "## macOS installation",
            "",
            "Apple Silicon and macOS 26.2 or later are required. Quit Editarr, open the DMG, "
            "and copy Editarr to Applications. Apply and verify a local ad-hoc signature:",
            "",
            "```sh",
            "xattr -dr com.apple.quarantine /Applications/Editarr.app",
            "codesign --force --deep --sign - /Applications/Editarr.app",
            "codesign --verify --deep --strict --verbose=2 /Applications/Editarr.app",
            "```",
            "",
            "The release includes license notices, corresponding third-party sources, "
            "and SHA-256 checksums.",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("head")
    plan.add_argument("--before")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("worktree", type=Path)
    prepare.add_argument("revision")
    prepare.add_argument("version")
    notes = commands.add_parser("notes")
    notes.add_argument("revision")
    notes.add_argument("repository")
    args = parser.parse_args()
    if args.command == "plan":
        print(json.dumps({"include": release_plan(Path.cwd(), args.head, args.before)}))
    elif args.command == "prepare":
        print(prepare_release(args.worktree, args.revision, args.version))
    else:
        print(release_notes(Path.cwd(), args.revision, args.repository))


if __name__ == "__main__":
    main()
