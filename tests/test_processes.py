import subprocess
import sys
from types import SimpleNamespace

import pytest

from scanner.pipeline import processes, runner


class FakeCommandProcess:
    pid = 42

    def __init__(self, stderr_lines, returncode=0):
        self.stderr = iter(stderr_lines)
        self.returncode = returncode
        self._running = True

    def poll(self):
        if self._running:
            self._running = False
            return None
        return self.returncode


def test_run_command_drains_stderr_without_a_callback_and_preserves_failure_details(
    monkeypatch,
):
    process = FakeCommandProcess(["first detail\n", "last detail\n"], returncode=7)
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(subprocess.CalledProcessError) as raised:
        processes.run_command(["scanner", "--fail"])

    assert raised.value.returncode == 7
    assert raised.value.stderr == "first detail\nlast detail"


def test_run_command_relays_output_and_returns_success(monkeypatch):
    process = FakeCommandProcess(["frame 1\n", "frame 2\n"])
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *_args, **_kwargs: process)
    output: list[str] = []

    result = processes.run_command(["scanner"], output_callback=output.append)

    assert result.returncode == 0
    assert output == ["frame 1", "frame 2"]


def test_run_command_keeps_reading_stderr_after_undecodable_bytes():
    script = (
        "import sys; "
        "sys.stderr.buffer.write(b'Input #0, from /media/caf\\xe9.mkv\\n'); "
        "sys.stderr.buffer.write(b'after\\n')"
    )
    output: list[str] = []

    processes.run_command([sys.executable, "-c", script], output_callback=output.append)

    assert output == ["Input #0, from /media/caf\ufffd.mkv", "after"]


def test_run_command_terminates_on_cooperative_cancellation(monkeypatch):
    process = FakeCommandProcess([])
    process.poll = lambda: None
    terminated: list[object] = []
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(processes, "terminate_process_tree", terminated.append)

    with pytest.raises(processes.ScanCancelledError, match="Scan cancelled"):
        processes.run_command(["scanner"], should_cancel=lambda: True)

    assert terminated == [process]


def test_run_command_terminates_when_cancellation_check_loses_authority(monkeypatch):
    process = FakeCommandProcess([])
    process.poll = lambda: None
    terminated: list[object] = []
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(processes, "terminate_process_tree", terminated.append)
    refusal = PermissionError("Download permission revoked")

    def require_authority():
        raise refusal

    with pytest.raises(PermissionError) as raised:
        processes.run_command(["scanner"], should_cancel=require_authority)

    assert raised.value is refusal
    assert terminated == [process]


def test_ffmpeg_retries_cpu_decode_after_videotoolbox_failure(monkeypatch):
    calls: list[tuple[list[str], object, object]] = []
    command = ["ffmpeg", "-ss", "2", "-i", "movie.mkv", "-y"]

    def callback(_line):
        return None

    def cancelled():
        return False

    def fake_run_command(args, *, should_cancel=None, output_callback=None):
        calls.append((args, should_cancel, output_callback))
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(processes.sys, "platform", "darwin")
    monkeypatch.setattr(processes, "run_command", fake_run_command)

    processes.run_ffmpeg_with_optional_macos_hwaccel(
        command, should_cancel=cancelled, output_callback=callback
    )

    assert calls == [
        (
            ["ffmpeg", "-ss", "2", "-hwaccel", "videotoolbox", "-i", "movie.mkv", "-y"],
            cancelled,
            callback,
        ),
        (command, cancelled, callback),
    ]


def test_terminate_process_tree_tolerates_exit_during_process_group_lookup(monkeypatch):
    process = SimpleNamespace(pid=42, poll=lambda: None)
    monkeypatch.setattr(
        processes.os,
        "getpgid",
        lambda _pid: (_ for _ in ()).throw(ProcessLookupError()),
        raising=False,
    )

    processes.terminate_process_tree(process, platform_name="posix")


def test_terminate_process_tree_signals_the_dedicated_process_group(monkeypatch):
    signals: list[tuple[int, int]] = []
    process = SimpleNamespace(pid=42, poll=lambda: None, wait=lambda timeout=None: 0)
    monkeypatch.setattr(processes.os, "getpgid", lambda _pid: 100, raising=False)
    monkeypatch.setattr(processes.os, "getpgrp", lambda: 99, raising=False)
    monkeypatch.setattr(
        processes.os,
        "killpg",
        lambda process_group, sent_signal: signals.append((process_group, sent_signal)),
        raising=False,
    )

    processes.terminate_process_tree(process, platform_name="posix")

    assert signals == [(100, processes.signal.SIGTERM)]


def test_terminate_process_tree_escalates_a_stuck_process(monkeypatch):
    events: list[str] = []

    class StuckProcess:
        pid = 42

        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")
            if timeout is not None:
                raise subprocess.TimeoutExpired("scanner", timeout)
            return 0

        def kill(self):
            events.append("kill")

    processes.terminate_process_tree(StuckProcess(), platform_name="nt")

    assert events == ["terminate", "wait", "kill", "wait"]


def test_isolated_worker_termination_tolerates_an_already_exited_process(monkeypatch):
    process = SimpleNamespace(
        pid=42,
        terminate=lambda: (_ for _ in ()).throw(ProcessLookupError()),
        join=lambda timeout=None: None,
        is_alive=lambda: False,
    )
    terminated: list[object] = []
    monkeypatch.setattr(runner, "terminate_isolated_process", terminated.append)

    runner._terminate_worker(process)
    assert terminated == [process]
