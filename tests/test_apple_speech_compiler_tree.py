"""Exercise macOS compiler shutdown without running a real Swift build."""

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from scanner.runtime.apple_speech.helper import _COMPILER_WATCHDOG

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS compiler supervision")

_PARENT = """
import os, subprocess, sys
watchdog, source, marker = sys.argv[1:]
owner = subprocess.Popen(
    [sys.executable, watchdog, str(os.getpid()), sys.executable, "-c", source, marker]
)
raise SystemExit(owner.wait())
"""
_COMPILER = """
import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
marker = Path(sys.argv[1])
staging = marker.with_suffix(".tmp")
staging.write_text(json.dumps([os.getppid(), os.getpid(), child.pid]))
staging.replace(marker)
time.sleep(30)
"""


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize("abort", ["cancel", "forced-parent-exit"])
def test_source_compiler_tree_exits_when_cancelled_or_its_parent_dies(tmp_path, abort):
    marker = tmp_path / "compiler-pids.json"
    with subprocess.Popen(
        [sys.executable, "-c", _PARENT, str(_COMPILER_WATCHDOG), _COMPILER, str(marker)],
    ) as parent:
        try:
            deadline = time.monotonic() + 8
            while not marker.exists() and parent.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), "test compiler did not start"
            watchdog, compiler, child = json.loads(marker.read_text())
            assert os.getpgid(compiler) == os.getpgid(child) == compiler
            assert os.getpgid(watchdog) == watchdog
            if abort == "cancel":
                os.killpg(watchdog, signal.SIGTERM)
            else:
                parent.kill()
            assert parent.wait(timeout=5) != 0

            deadline = time.monotonic() + 8
            processes = (watchdog, compiler, child)
            while any(map(_running, processes)) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not any(map(_running, processes)), "compiler tree survived"
        finally:
            if parent.poll() is None:
                parent.kill()


def test_source_compiler_watchdog_returns_the_compiler_exit_code():
    result = subprocess.run(
        [
            sys.executable, str(_COMPILER_WATCHDOG), str(os.getpid()),
            sys.executable, "-c", "raise SystemExit(7)",
        ],
        check=False,
        timeout=5,
    )
    assert result.returncode == 7
