"""Cancellable subprocess helpers for scan pipeline tools."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Sequence
from queue import Empty, Queue
from threading import Thread


class ScanCancelledError(RuntimeError):
    """Raised when a cancellable pipeline command is stopped."""


def terminate_process_tree(
    process: subprocess.Popen[bytes], *, platform_name: str | None = None
) -> None:
    """Stop a process and, on POSIX, every process it started.

    ``platform_name`` is a narrow test seam. Production callers leave it unset
    and therefore always use the host platform rather than mutable global state.
    """
    if process.poll() is not None:
        return

    process_group: int | None = None
    if (os.name if platform_name is None else platform_name) == "posix":
        try:
            candidate = os.getpgid(process.pid)
        except ProcessLookupError:
            return
        if candidate != os.getpgrp():
            process_group = candidate

    if process_group is not None:
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        try:
            process.terminate()
        except ProcessLookupError:
            return

    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                return
        else:
            try:
                process.kill()
            except ProcessLookupError:
                return
        process.wait()


def run_command(
    command: Sequence[str],
    *,
    should_cancel: Callable[[], bool] | None = None,
    output_callback: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a command, polling for cancellation and cleaning up its process tree."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        # Dedicated scan workers each own a process group. Keep FFmpeg in that
        # group so deleting an orphaned worker also stops its active command.
        start_new_session=False,
        # Paths and metadata in FFmpeg logs need not match the locale encoding;
        # a decode error would otherwise stop the reader and drop later lines.
        encoding="utf-8",
        errors="replace",
    )
    output: Queue[str] = Queue()
    stderr_tail: deque[str] = deque(maxlen=200)

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            output.put(line.rstrip())

    reader = Thread(target=read_stderr, daemon=True)
    reader.start()

    def dispatch_output() -> None:
        while True:
            try:
                line = output.get_nowait()
            except Empty:
                return
            stderr_tail.append(line)
            if output_callback:
                output_callback(line)

    while process.poll() is None:
        try:
            dispatch_output()
            cancelled = should_cancel and should_cancel()
        except BaseException:
            terminate_process_tree(process)
            reader.join(timeout=1)
            raise
        if cancelled:
            terminate_process_tree(process)
            reader.join(timeout=1)
            dispatch_output()
            raise ScanCancelledError("Scan cancelled")
        time.sleep(0.1)

    reader.join(timeout=1)
    dispatch_output()

    if process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode,
            command,
            stderr="\n".join(stderr_tail),
        )
    return subprocess.CompletedProcess(command, process.returncode)


def run_ffmpeg_with_optional_macos_hwaccel(
    command: list[str],
    *,
    should_cancel: Callable[[], bool] | None = None,
    output_callback: Callable[[str], None] | None = None,
    before_attempt: Callable[[], None] | None = None,
) -> None:
    """Run FFmpeg with VideoToolbox on macOS, retrying unsupported inputs on the CPU.

    The command must include its input marker (``-i``) for hardware decoding.
    Cancellation and output callbacks are forwarded to each attempt, and
    ``before_attempt`` runs before each one so callers can discard state (such
    as parsed output or partial files) left by a failed hardware attempt.
    """
    if sys.platform != "darwin" or "-i" not in command:
        if before_attempt:
            before_attempt()
        run_command(command, should_cancel=should_cancel, output_callback=output_callback)
        return

    input_index = command.index("-i")
    hardware_command = [
        *command[:input_index],
        "-hwaccel",
        "videotoolbox",
        *command[input_index:],
    ]
    try:
        if before_attempt:
            before_attempt()
        run_command(
            hardware_command,
            should_cancel=should_cancel,
            output_callback=output_callback,
        )
    except subprocess.CalledProcessError:
        if before_attempt:
            before_attempt()
        run_command(command, should_cancel=should_cancel, output_callback=output_callback)
