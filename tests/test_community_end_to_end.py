"""Required cross-stack evidence when the local Node and archive toolchain is provisioned."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    not all(shutil.which(command) for command in ("node", "age", "expect")),
    reason="Community E2E requires Node >=24, age and Expect; see community-service/README.md",
)
def test_independent_editarr_instances_against_real_community_http_and_sqlite():
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, COMMUNITY_E2E_PYTHON=sys.executable)
    result = subprocess.run(
        [
            "node",
            "--test",
            "--test-name-pattern=multi-instance",
            "community-service/test/runtime/journeys.test.mjs",
        ],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=150,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pass 1" in result.stdout
