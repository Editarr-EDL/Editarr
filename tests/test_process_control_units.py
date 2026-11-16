"""Native subprocess evidence for token-bound worker process ownership."""

from __future__ import annotations

import ctypes
import logging
import os

import pytest

from scanner.runtime import process_control
from scanner.runtime.process_control import ProcessControl, WorkerIdentity
from tests.process_control_support import (
    _FakeLibrary,
)


def test_control_transport_selects_named_pipe_on_windows_without_changing_posix_contract():
    assert process_control.control_transport("nt") == "windows-named-pipe"
    assert process_control.control_transport("posix") == "unix-domain-socket"
    endpoint = process_control.worker_control_path(
        "12345678-1234-1234-1234-123456789abc", "opaque", platform_name="nt"
    )
    assert isinstance(endpoint, str) and endpoint.startswith(r"\\.\pipe\EditarrWorker-")


def test_windows_winapi_declarations_keep_handles_pointer_sized():
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    process_control._WindowsApi(kernel32, advapi32, ctypes)

    assert kernel32.CreateJobObjectW.restype is ctypes.c_void_p
    assert kernel32.OpenProcess.restype is ctypes.c_void_p
    assert kernel32.IsProcessInJob.restype is ctypes.c_int
    assert kernel32.AssignProcessToJobObject.restype is ctypes.c_int
    assert kernel32.TerminateJobObject.restype is ctypes.c_int
    assert kernel32.CloseHandle.restype is ctypes.c_int
    assert kernel32.CreateNamedPipeW.restype is ctypes.c_void_p
    assert kernel32.CreateFileW.restype is ctypes.c_void_p
    assert kernel32.FlushFileBuffers.restype is ctypes.c_int


def test_windows_pipe_uses_logon_sid_dacl_and_rejects_remote_clients():
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)

    _attributes, descriptor = process_control._windows_logon_security_attributes(
        api, "S-1-5-5-42-84"
    )
    assert advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.calls[0][0] == (
        "D:P(A;;GA;;;S-1-5-5-42-84)"
    )
    assert process_control._windows_pipe_mode() & 0x00000008
    api.kernel32.LocalFree(descriptor)
    assert len(kernel32.LocalFree.calls) == 1


def test_windows_pipe_prepares_secured_instance_before_worker_reports_ready():
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)
    listener = process_control._WindowsPipeListener(
        r"\\.\pipe\EditarrWorker-test",
        b"token",
        api=api,
        logon_sid_provider=lambda _api: "S-1-5-5-42-84",
    )

    listener.prepare()

    assert len(kernel32.CreateNamedPipeW.calls) == 1
    assert kernel32.CreateNamedPipeW.calls[0][2] & 0x00000008
    listener.close()
    assert len(kernel32.CloseHandle.calls) == 1


def test_windows_job_handles_are_closed_through_injectable_pointer_safe_api():
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)

    worker_job = process_control._assign_windows_job(
        17, "Local\\EditarrWorker-test", api=api, platform_name="nt"
    )
    assert worker_job is not None
    assert len(kernel32.AssignProcessToJobObject.calls) == 1
    assert kernel32.OpenProcess.calls[0][0] & 0x0400  # QUERY_INFORMATION for IsProcessInJob
    assert len(kernel32.CloseHandle.calls) == 1  # process only; worker retains the Job.
    worker_job.close()
    assert len(kernel32.CloseHandle.calls) == 2

    assert process_control._terminate_windows_job(
        "Local\\EditarrWorker-test", api=api, platform_name="nt"
    )
    assert kernel32.OpenJobObjectW.calls[0][0] == process_control._JOB_OBJECT_TERMINATE
    assert len(kernel32.TerminateJobObject.calls) == 1
    assert len(kernel32.CloseHandle.calls) == 3


def test_windows_job_verification_reopens_a_worker_retained_job_without_creating_one():
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)

    def is_process_in_job(_process, _job, in_job):
        in_job._obj.value = 1
        return 1

    kernel32.IsProcessInJob = is_process_in_job
    process_control._verify_windows_worker_job(
        17, "Local\\EditarrWorker-test", api=api, platform_name="nt"
    )

    assert not kernel32.CreateJobObjectW.calls
    assert kernel32.OpenJobObjectW.calls[0][0] == process_control._JOB_OBJECT_QUERY
    assert len(kernel32.CloseHandle.calls) == 2


def test_windows_job_termination_reports_a_token_free_native_error(caplog, monkeypatch):
    kernel32 = _FakeLibrary()
    kernel32.OpenJobObjectW.result = 0
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)
    monkeypatch.setattr(api.ctypes, "get_last_error", lambda: 5, raising=False)

    with caplog.at_level(logging.WARNING, logger=process_control.__name__):
        assert not process_control._terminate_windows_job(
            "Local\\EditarrWorker-test", api=api, platform_name="nt"
        )

    assert "Win32 error 5" in caplog.text
    assert "token" not in caplog.text.lower()


def test_windows_job_assignment_skips_a_worker_already_in_the_derived_job():
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)

    def is_process_in_job(_process, _job, in_job):
        in_job._obj.value = 1
        return 1

    kernel32.IsProcessInJob = is_process_in_job
    worker_job = process_control._assign_windows_job(
        17, "Local\\EditarrWorker-test", api=api, platform_name="nt"
    )

    assert worker_job is not None
    assert not kernel32.AssignProcessToJobObject.calls
    assert len(kernel32.CloseHandle.calls) == 1
    worker_job.close()
    assert len(kernel32.CloseHandle.calls) == 2


def test_windows_pipe_poll_waits_until_a_frame_is_available(monkeypatch):
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)
    clock = {"now": 0.0, "peeks": 0}

    def peek(_handle, _buffer, _size, _read, available, _left):
        clock["peeks"] += 1
        available._obj.value = 4 if clock["peeks"] == 2 else 0
        return 1

    kernel32.PeekNamedPipe = peek
    monkeypatch.setattr(process_control.time, "monotonic", lambda: clock["now"])
    def advance(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(process_control.time, "sleep", advance)

    assert process_control._WindowsPipeConnection(api, 1).poll(0.1)
    assert clock["peeks"] == 2


def test_windows_pipe_poll_expires_when_no_frame_arrives(monkeypatch):
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)
    ticks = iter((0.0, 0.1))

    def peek(_handle, _buffer, _size, _read, available, _left):
        available._obj.value = 0
        return 1

    kernel32.PeekNamedPipe = peek
    monkeypatch.setattr(process_control.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(process_control.time, "sleep", lambda _seconds: None)

    assert not process_control._WindowsPipeConnection(api, 1).poll(0.1)


def test_windows_pipe_connection_preserves_final_native_error_for_safe_diagnostics(monkeypatch):
    kernel32 = _FakeLibrary()
    kernel32.CreateFileW.result = 0
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(process_control.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(api.ctypes, "get_last_error", lambda: 5, raising=False)

    with pytest.raises(OSError) as error:
        process_control._connect_windows_pipe(r"\\.\pipe\EditarrWorker-test", b"token", api=api)

    assert error.value.errno == 5
    assert "token" not in str(error.value)


def test_discovery_persists_authenticated_worker_pid_not_redirector(monkeypatch):
    monkeypatch.setattr(process_control, "_authenticated_worker_pid", lambda *_args: 6344)
    control = ProcessControl()

    discovered = control.discover_identity(
        "scan", "token", launcher_pid=2808, platform_name="nt"
    )

    assert discovered == WorkerIdentity("scan", 6344, None, "token")
    assert control.identity("scan", 2808, None, "token") is None
    assert control.identity("scan", 6344, None, "token") == discovered


def test_posix_discovery_rejects_authenticated_pid_other_than_launcher(monkeypatch):
    monkeypatch.setattr(process_control, "_authenticated_worker_pid", lambda *_args: 6344)

    assert ProcessControl().discover_identity(
        "scan", "token", launcher_pid=2808, platform_name="posix"
    ) is None


def test_discovery_allows_cold_start_longer_than_five_seconds(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(process_control.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(process_control.time, "sleep", lambda delay: elapsed.__setitem__(
        0, elapsed[0] + delay
    ))
    monkeypatch.setattr(
        process_control, "_authenticated_worker_pid",
        lambda *_args: 2808 if elapsed[0] >= 6 else None,
    )

    assert ProcessControl().discover_identity(
        "scan", "token", launcher_pid=2808, platform_name="posix"
    ) == WorkerIdentity("scan", 2808, None, "token")


def test_discovery_cold_start_grace_is_bounded(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(process_control.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(process_control.time, "sleep", lambda delay: elapsed.__setitem__(
        0, elapsed[0] + delay
    ))
    monkeypatch.setattr(process_control, "_authenticated_worker_pid", lambda *_args: None)

    assert ProcessControl().discover_identity(
        "scan", "token", launcher_pid=2808, platform_name="posix"
    ) is None
    assert elapsed[0] < process_control.WORKER_START_TIMEOUT + 0.1


def test_posix_discovery_reports_early_worker_exit(monkeypatch):
    monkeypatch.setattr(process_control, "_authenticated_worker_pid", lambda *_args: None)

    class ExitedProcess:
        def poll(self):
            return 7

    with pytest.raises(process_control.ProcessControlError, match="exit code 7"):
        ProcessControl().discover_identity(
            "scan", "token", launcher_pid=2808, platform_name="posix",
            launcher_process=ExitedProcess(),
        )


def test_windows_discovery_waits_after_redirector_exits(monkeypatch):
    reported_pids = iter([None, 6344])
    monkeypatch.setattr(
        process_control, "_authenticated_worker_pid", lambda *_args: next(reported_pids)
    )

    class Redirector:
        def poll(self):
            raise AssertionError("redirector exit is not evidence of worker exit")

    assert ProcessControl().discover_identity(
        "scan", "token", launcher_pid=2808, platform_name="nt", launcher_process=Redirector()
    ) == WorkerIdentity("scan", 6344, None, "token")


def test_windows_server_connection_flushes_then_disconnects_before_closing():
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)

    process_control._WindowsPipeConnection(api, 7).close_server()

    assert len(kernel32.FlushFileBuffers.calls) == 1
    assert len(kernel32.DisconnectNamedPipe.calls) == 1
    assert len(kernel32.CloseHandle.calls) == 1


def test_windows_client_connection_close_does_not_flush_or_disconnect():
    kernel32 = _FakeLibrary()
    advapi32 = _FakeLibrary()
    api = process_control._WindowsApi(kernel32, advapi32, ctypes)

    process_control._WindowsPipeConnection(api, 7).close()

    assert not kernel32.FlushFileBuffers.calls
    assert not kernel32.DisconnectNamedPipe.calls
    assert len(kernel32.CloseHandle.calls) == 1


def test_isolated_visual_worker_enters_one_posix_session(monkeypatch):
    if os.name != "posix":
        pytest.skip("POSIX session isolation is not available on Windows")
    calls: list[str] = []
    monkeypatch.setattr(process_control.os, "setsid", lambda: calls.append("setsid"))

    process_control.enter_isolated_visual_worker()

    assert calls == ["setsid"]


def test_posix_force_stop_escalates_after_bounded_term_wait(monkeypatch):
    if os.name != "posix":
        return
    identity = WorkerIdentity("scan", 123, 456, "token")
    signals: list[int] = []
    ticks = iter((0.0, 4.0))
    monkeypatch.setattr(process_control, "_prove_identity", lambda _identity: True)
    monkeypatch.setattr(process_control, "_proven_posix_group", lambda _identity: True)
    monkeypatch.setattr(process_control.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(process_control.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        process_control.os, "killpg", lambda _group, sent_signal: signals.append(sent_signal)
    )

    assert ProcessControl().force_stop(identity)
    assert signals == [process_control.signal.SIGTERM, process_control.signal.SIGKILL]


def test_posix_force_stop_does_not_kill_when_identity_changes_after_term_wait(monkeypatch):
    if os.name != "posix":
        return
    identity = WorkerIdentity("scan", 123, 456, "token")
    signals: list[int] = []
    proofs = iter((True, False))
    ticks = iter((0.0, 4.0))
    monkeypatch.setattr(process_control, "_prove_identity", lambda _identity: next(proofs))
    monkeypatch.setattr(process_control, "_proven_posix_group", lambda _identity: True)
    monkeypatch.setattr(process_control, "cleanup_worker_control_endpoint", lambda *_args: None)
    monkeypatch.setattr(process_control.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(process_control.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        process_control.os, "killpg", lambda _group, sent_signal: signals.append(sent_signal)
    )

    assert ProcessControl().force_stop(identity)
    assert signals == [process_control.signal.SIGTERM]


