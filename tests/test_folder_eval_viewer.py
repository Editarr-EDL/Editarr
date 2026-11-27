"""Run the viewer's pure-function tests (tests/folder_eval_viewer/*.test.mjs) with Node."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TESTS = sorted((Path(__file__).parent / "folder_eval_viewer").glob("*.test.mjs"))


def _node_version(node: str) -> tuple[int, int]:
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=False)
    try:
        major, minor = version.stdout.strip().lstrip("v").split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return 0, 0


def test_viewer_rules_with_node():
    node = shutil.which("node")
    # The viewer ships plain .js ES modules without a package.json; Node detects their
    # module syntax from 22.7.
    if node is None or _node_version(node) < (22, 7):
        pytest.skip("Node 22.7 or newer is required for the viewer tests")
    result = subprocess.run(
        [node, "--test", *map(str, TESTS)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
