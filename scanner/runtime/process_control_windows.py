"""Private Windows named-pipe transport and worker Job Object operations."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import pickle
import struct
import time
from dataclasses import dataclass
from typing import Any

CONTROL_TIMEOUT = 1.0
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_TOKEN_QUERY = 0x0008
_TOKEN_GROUPS = 2
_SE_GROUP_LOGON_ID = 0xC0000000
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_TERMINATE = 0x0008
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_INFORMATION = 0x0400

logger = logging.getLogger("scanner.runtime.process_control")


class ProcessControlError(RuntimeError):
    """A platform process-control operation could not be completed."""


@dataclass(slots=True)
class _WindowsJobHandle:
    """The worker-owned handle that keeps its named Job Object reopenable."""

    api: _WindowsApi
    handle: Any

    def close(self) -> None:
        if self.handle:
            self.api.kernel32.CloseHandle(self.handle)
            self.handle = None


def _windows_pipe_mode() -> int:
    """Byte-mode, blocking pipe mode with an explicit remote-client rejection."""

    return _PIPE_REJECT_REMOTE_CLIENTS


class _WindowsApi:
    """Pointer-safe Win32 declarations shared by Jobs and private named pipes."""

    def __init__(self, kernel32, advapi32, ctypes_module):
        """Declare pointer-safe signatures for the supplied Win32 library handles."""
        self.kernel32 = kernel32
        self.advapi32 = advapi32
        self.ctypes = ctypes_module
        c_void_p = ctypes_module.c_void_p
        c_ulong = ctypes_module.c_ulong
        c_wchar_p = ctypes_module.c_wchar_p
        c_bool = ctypes_module.c_int
        dword_pointer = ctypes_module.POINTER(c_ulong)

        self.kernel32.CreateJobObjectW.argtypes = [c_void_p, c_wchar_p]
        self.kernel32.CreateJobObjectW.restype = c_void_p
        self.kernel32.OpenJobObjectW.argtypes = [c_ulong, c_bool, c_wchar_p]
        self.kernel32.OpenJobObjectW.restype = c_void_p
        self.kernel32.OpenProcess.argtypes = [c_ulong, c_bool, c_ulong]
        self.kernel32.OpenProcess.restype = c_void_p
        self.kernel32.IsProcessInJob.argtypes = [c_void_p, c_void_p, ctypes_module.POINTER(c_bool)]
        self.kernel32.IsProcessInJob.restype = c_bool
        self.kernel32.AssignProcessToJobObject.argtypes = [c_void_p, c_void_p]
        self.kernel32.AssignProcessToJobObject.restype = c_bool
        self.kernel32.TerminateJobObject.argtypes = [c_void_p, c_ulong]
        self.kernel32.TerminateJobObject.restype = c_bool
        self.kernel32.CloseHandle.argtypes = [c_void_p]
        self.kernel32.CloseHandle.restype = c_bool

        self.kernel32.CreateNamedPipeW.argtypes = [
            c_wchar_p,
            c_ulong,
            c_ulong,
            c_ulong,
            c_ulong,
            c_ulong,
            c_ulong,
            c_void_p,
        ]
        self.kernel32.CreateNamedPipeW.restype = c_void_p
        self.kernel32.ConnectNamedPipe.argtypes = [c_void_p, c_void_p]
        self.kernel32.ConnectNamedPipe.restype = c_bool
        self.kernel32.CreateFileW.argtypes = [
            c_wchar_p,
            c_ulong,
            c_ulong,
            c_void_p,
            c_ulong,
            c_ulong,
            c_void_p,
        ]
        self.kernel32.CreateFileW.restype = c_void_p
        self.kernel32.ReadFile.argtypes = [c_void_p, c_void_p, c_ulong, dword_pointer, c_void_p]
        self.kernel32.ReadFile.restype = c_bool
        self.kernel32.WriteFile.argtypes = [c_void_p, c_void_p, c_ulong, dword_pointer, c_void_p]
        self.kernel32.WriteFile.restype = c_bool
        self.kernel32.FlushFileBuffers.argtypes = [c_void_p]
        self.kernel32.FlushFileBuffers.restype = c_bool
        self.kernel32.PeekNamedPipe.argtypes = [
            c_void_p,
            c_void_p,
            c_ulong,
            c_void_p,
            dword_pointer,
            c_void_p,
        ]
        self.kernel32.PeekNamedPipe.restype = c_bool
        self.kernel32.DisconnectNamedPipe.argtypes = [c_void_p]
        self.kernel32.DisconnectNamedPipe.restype = c_bool
        self.kernel32.LocalFree.argtypes = [c_void_p]
        self.kernel32.LocalFree.restype = c_void_p
        self.kernel32.GetCurrentProcess.argtypes = []
        self.kernel32.GetCurrentProcess.restype = c_void_p
        self.advapi32.OpenProcessToken.argtypes = [
            c_void_p,
            c_ulong,
            ctypes_module.POINTER(c_void_p),
        ]
        self.advapi32.OpenProcessToken.restype = c_bool
        self.advapi32.GetTokenInformation.argtypes = [
            c_void_p,
            c_ulong,
            c_void_p,
            c_ulong,
            dword_pointer,
        ]
        self.advapi32.GetTokenInformation.restype = c_bool
        self.advapi32.ConvertSidToStringSidW.argtypes = [
            c_void_p,
            ctypes_module.POINTER(c_wchar_p),
        ]
        self.advapi32.ConvertSidToStringSidW.restype = c_bool
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            c_wchar_p,
            c_ulong,
            ctypes_module.POINTER(c_void_p),
            c_void_p,
        ]
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = c_bool


def _windows_api():
    if os.name != "nt":  # pragma: no cover - guarded by Windows transport selection
        raise ProcessControlError("Windows process control is unavailable")
    import ctypes

    return _WindowsApi(
        ctypes.WinDLL("kernel32", use_last_error=True),
        ctypes.WinDLL("advapi32", use_last_error=True),
        ctypes,
    )


def windows_worker_may_be_running(pid: int) -> bool:
    """Read process liveness without issuing a signal to an unverified PID."""
    api = _windows_api()
    c = api.ctypes
    synchronize = 0x00100000
    process = api.kernel32.OpenProcess(synchronize, False, pid)
    if not process:
        return c.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such process.
    try:
        wait = api.kernel32.WaitForSingleObject
        wait.argtypes = [c.c_void_p, c.c_ulong]
        wait.restype = c.c_ulong
        return wait(process, 0) != 0
    finally:
        api.kernel32.CloseHandle(process)


def _windows_logon_sid(api: _WindowsApi) -> str:
    """Return this process token's logon SID for Terminal Services isolation."""

    c = api.ctypes
    token = c.c_void_p()
    if not api.advapi32.OpenProcessToken(
        api.kernel32.GetCurrentProcess(), _TOKEN_QUERY, c.byref(token)
    ):
        raise ProcessControlError("could not read current Windows logon token")
    try:
        size = c.c_ulong()
        api.advapi32.GetTokenInformation(token, _TOKEN_GROUPS, None, 0, c.byref(size))
        if not size.value:
            raise ProcessControlError("could not read Windows token groups")
        buffer = c.create_string_buffer(size.value)
        if not api.advapi32.GetTokenInformation(
            token, _TOKEN_GROUPS, buffer, size.value, c.byref(size)
        ):
            raise ProcessControlError("could not resolve Windows token groups")

        class SidAndAttributes(c.Structure):
            _fields_ = [("sid", c.c_void_p), ("attributes", c.c_ulong)]

        class TokenGroups(c.Structure):
            _fields_ = [("count", c.c_ulong), ("groups", SidAndAttributes * 1)]

        groups = c.cast(buffer, c.POINTER(TokenGroups)).contents
        first_offset = TokenGroups.groups.offset
        for index in range(groups.count):
            address = c.addressof(buffer) + first_offset + index * c.sizeof(SidAndAttributes)
            group = c.cast(address, c.POINTER(SidAndAttributes)).contents
            if group.attributes & _SE_GROUP_LOGON_ID:
                value = c.c_wchar_p()
                if not api.advapi32.ConvertSidToStringSidW(group.sid, c.byref(value)):
                    raise ProcessControlError("could not stringify Windows logon SID")
                try:
                    return value.value
                finally:
                    api.kernel32.LocalFree(c.cast(value, c.c_void_p))
    finally:
        api.kernel32.CloseHandle(token)
    raise ProcessControlError("current Windows token has no logon SID")


def _windows_logon_security_attributes(api: _WindowsApi, logon_sid: str):
    """Create a protected DACL scoped to this interactive logon session."""

    c = api.ctypes
    descriptor = c.c_void_p()
    sddl = f"D:P(A;;GA;;;{logon_sid})"
    if not api.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, c.byref(descriptor), None
    ):
        raise ProcessControlError("could not create logon-session named-pipe ACL")

    class SecurityAttributes(c.Structure):
        _fields_ = [
            ("nLength", c.c_ulong),
            ("lpSecurityDescriptor", c.c_void_p),
            ("bInheritHandle", c.c_int),
        ]

    attributes = SecurityAttributes(c.sizeof(SecurityAttributes), descriptor, False)
    return attributes, descriptor


class _WindowsPipeConnection:
    def __init__(self, api: _WindowsApi, handle):
        self.api = api
        self.handle = handle

    def _read_exact(self, length: int) -> bytes:
        """Read exactly the requested number of bytes from a pipe frame."""
        parts = bytearray()
        while len(parts) < length:
            buffer = self.api.ctypes.create_string_buffer(length - len(parts))
            read = self.api.ctypes.c_ulong()
            if not self.api.kernel32.ReadFile(
                self.handle, buffer, len(buffer), self.api.ctypes.byref(read), None
            ):
                raise OSError("named-pipe read failed")
            if not read.value:
                raise EOFError
            parts.extend(buffer.raw[: read.value])
        return bytes(parts)

    def send_bytes(self, payload: bytes) -> None:
        message = struct.pack("!I", len(payload)) + payload
        written = self.api.ctypes.c_ulong()
        buffer = self.api.ctypes.create_string_buffer(message)
        if not self.api.kernel32.WriteFile(
            self.handle, buffer, len(message), self.api.ctypes.byref(written), None
        ) or written.value != len(message):
            raise OSError("named-pipe write failed")

    def recv_bytes(self) -> bytes:
        length = struct.unpack("!I", self._read_exact(4))[0]
        if length > 16 * 1024 * 1024:
            raise ValueError("named-pipe message is too large")
        return self._read_exact(length)

    def send(self, value) -> None:
        self.send_bytes(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))

    def recv(self):
        return pickle.loads(self.recv_bytes())

    def poll(self, timeout: float = 0.0) -> bool:
        """Wait at most ``timeout`` for a framed-message header or broken pipe."""

        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            available = self.api.ctypes.c_ulong()
            if not self.api.kernel32.PeekNamedPipe(
                self.handle, None, 0, None, self.api.ctypes.byref(available), None
            ):
                # Mirror multiprocessing.Connection: an EOF is readable so the
                # caller can receive it and transition its controller capability.
                return True
            if available.value >= 4:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.01, remaining))

    def close(self) -> None:
        if self.handle:
            self.api.kernel32.CloseHandle(self.handle)
            self.handle = None

    def close_server(self) -> None:
        """Deliver a server response before disconnecting this pipe instance.

        Closing a named-pipe server handle immediately after ``WriteFile`` can
        discard the final response.  The client owns ordinary close behavior;
        only the server flushes and disconnects its instance.
        """

        if not self.handle:
            return
        try:
            self.api.kernel32.FlushFileBuffers(self.handle)
        finally:
            try:
                self.api.kernel32.DisconnectNamedPipe(self.handle)
            finally:
                self.close()


class _WindowsPipeListener:
    def __init__(
        self,
        address: str,
        token: bytes,
        api: _WindowsApi | None = None,
        logon_sid_provider=_windows_logon_sid,
    ):
        """Prepare a pipe listener using a token and the current logon session ACL."""
        self.address = address
        self.token = token
        self.api = _windows_api() if api is None else api
        self.logon_sid_provider = logon_sid_provider
        # Resolve the security principal before the worker reports itself ready.
        # A missing logon SID is permanent for this process, not a connection retry.
        self.logon_sid = self.logon_sid_provider(self.api)
        self._pending_handle = None

    def _create_handle(self):
        """Create one secured server instance, releasing only its ACL buffer."""

        c = self.api.ctypes
        attributes, descriptor = _windows_logon_security_attributes(self.api, self.logon_sid)
        try:
            handle = self.api.kernel32.CreateNamedPipeW(
                self.address,
                0x00000003,  # PIPE_ACCESS_DUPLEX
                _windows_pipe_mode(),  # byte/read-byte/wait + reject remote
                4,
                65536,
                65536,
                0,
                c.byref(attributes),
            )
        finally:
            self.api.kernel32.LocalFree(descriptor)
        invalid = c.c_void_p(-1).value
        if not handle or handle == invalid:
            raise ProcessControlError("could not create private named pipe")
        return handle

    def prepare(self) -> None:
        """Create the first instance before the worker advertises itself as live.

        A DACL or CreateNamedPipe failure is a worker-startup failure, not an
        asynchronous connection retry that would otherwise look like a controller
        identity timeout.
        """

        if self._pending_handle is None:
            self._pending_handle = self._create_handle()

    def accept(self):
        """Accept and authenticate one same-session client on the private named pipe."""
        c = self.api.ctypes
        handle = self._pending_handle
        self._pending_handle = None
        if handle is None:
            handle = self._create_handle()
        connected = self.api.kernel32.ConnectNamedPipe(handle, None)
        if not connected and c.get_last_error() != 535:  # ERROR_PIPE_CONNECTED
            self.api.kernel32.CloseHandle(handle)
            raise OSError("could not accept private named-pipe client")
        connection = _WindowsPipeConnection(self.api, handle)
        try:
            expected = hmac.new(self.token, b"connect", hashlib.sha256).digest()
            if not hmac.compare_digest(connection.recv_bytes(), expected):
                raise PermissionError("named-pipe authentication failed")
            connection.send_bytes(b"ok")
            return connection
        except BaseException:
            connection.close()
            raise

    def close(self) -> None:
        if self._pending_handle is not None:
            self.api.kernel32.CloseHandle(self._pending_handle)
            self._pending_handle = None


def _connect_windows_pipe(address: str, token: bytes, api: _WindowsApi | None = None):
    """Connect to a private pipe and complete its token challenge within the timeout."""
    pipe_api = _windows_api() if api is None else api
    c = pipe_api.ctypes
    invalid = c.c_void_p(-1).value
    deadline = time.monotonic() + CONTROL_TIMEOUT
    last_error = 0
    while True:
        handle = pipe_api.kernel32.CreateFileW(address, 0xC0000000, 0, None, 3, 0, None)
        if handle and handle != invalid:
            connection = _WindowsPipeConnection(pipe_api, handle)
            try:
                connection.send_bytes(hmac.new(token, b"connect", hashlib.sha256).digest())
                if connection.recv_bytes() != b"ok":
                    raise PermissionError("named-pipe authentication failed")
                return connection
            except BaseException:
                connection.close()
                raise
        # Preserve the native error for diagnostics at the boundary, but keep
        # ordinary identity failure opaque to production callers of ``_request``.
        last_error = getattr(c, "get_last_error", lambda: 0)()
        if time.monotonic() >= deadline:
            raise OSError(last_error, "could not connect to private named pipe")
        time.sleep(0.02)


def _assign_windows_job(
    pid: int,
    name: str,
    *,
    api: _WindowsApi | None = None,
    platform_name: str | None = None,
) -> _WindowsJobHandle | None:
    """Assign a worker and retain its named Job Object for the worker lifetime."""

    if (os.name if platform_name is None else platform_name) != "nt":  # pragma: no cover
        return None
    api = _windows_api() if api is None else api
    job = api.kernel32.CreateJobObjectW(None, name)
    if not job:
        raise ProcessControlError("could not create worker Job Object")
    try:
        process = api.kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_INFORMATION,
            False,
            pid,
        )
        if not process:
            raise ProcessControlError("could not open worker process for Job Object")
        try:
            in_job = api.ctypes.c_int()
            if not api.kernel32.IsProcessInJob(process, job, api.ctypes.byref(in_job)):
                raise ProcessControlError("could not inspect worker Job Object membership")
            if not in_job.value and not api.kernel32.AssignProcessToJobObject(job, process):
                raise ProcessControlError("could not assign worker to Job Object")
        finally:
            api.kernel32.CloseHandle(process)
    except Exception:
        api.kernel32.CloseHandle(job)
        raise
    return _WindowsJobHandle(api, job)


def _verify_windows_worker_job(
    pid: int,
    name: str,
    *,
    api: _WindowsApi | None = None,
    platform_name: str | None = None,
) -> None:
    """Verify a worker-held Job exists and owns the already-proven interpreter."""

    if (os.name if platform_name is None else platform_name) != "nt":  # pragma: no cover
        return
    api = _windows_api() if api is None else api
    job = api.kernel32.OpenJobObjectW(_JOB_OBJECT_QUERY, False, name)
    if not job:
        raise ProcessControlError("could not reopen worker Job Object for verification")
    try:
        process = api.kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION, False, pid)
        if not process:
            raise ProcessControlError("could not open worker process for Job verification")
        try:
            in_job = api.ctypes.c_int()
            if not api.kernel32.IsProcessInJob(process, job, api.ctypes.byref(in_job)):
                raise ProcessControlError("could not inspect worker Job Object membership")
            if not in_job.value:
                raise ProcessControlError("proven worker is not in its derived Job Object")
        finally:
            api.kernel32.CloseHandle(process)
    finally:
        api.kernel32.CloseHandle(job)


def _terminate_windows_job(
    name: str,
    *,
    api: _WindowsApi | None = None,
    platform_name: str | None = None,
) -> bool:
    """Open a proven worker Job Object and terminate its process tree."""
    if (os.name if platform_name is None else platform_name) != "nt":  # pragma: no cover
        return False
    api = _windows_api() if api is None else api
    job = api.kernel32.OpenJobObjectW(_JOB_OBJECT_TERMINATE, False, name)
    if not job:
        error = getattr(api.ctypes, "get_last_error", lambda: 0)()
        logger.warning(
            "Could not open proven worker Job Object for termination (Win32 error %d)", error
        )
        return False
    try:
        terminated = bool(api.kernel32.TerminateJobObject(job, 1))
        if not terminated:
            error = getattr(api.ctypes, "get_last_error", lambda: 0)()
            logger.warning(
                "Could not terminate proven worker Job Object (Win32 error %d)", error
            )
        return terminated
    finally:
        api.kernel32.CloseHandle(job)
