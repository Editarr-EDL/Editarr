"""Own and stop the source Swift compiler tree even if its scan process dies."""

import os
import signal
import subprocess
import sys
from contextlib import suppress


def run_compiler(parent_pid: int, command: list[str]) -> int:
    """Run a compiler command in an owned group and return its exit status."""
    # A forced scan-group shutdown can kill our parent with no cleanup handler.
    # Stay alive long enough to stop the compiler, even in that case.
    os.setsid()
    interrupted = False

    def stop(_signal, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if interrupted or os.getppid() != parent_pid:
        return 1

    with subprocess.Popen(command, start_new_session=True) as compiler:
        try:
            while not interrupted and os.getppid() == parent_pid:
                try:
                    return compiler.wait(timeout=0.1)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if compiler.returncode is None:
                # On shutdown, kill the group before its leader's PID can be reused.
                with suppress(ProcessLookupError):
                    os.killpg(compiler.pid, signal.SIGKILL)
        return 1


if __name__ == "__main__":
    raise SystemExit(run_compiler(int(sys.argv[1]), sys.argv[2:]))
