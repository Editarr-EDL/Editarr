"""Native subprocess evidence for token-bound worker process ownership."""

from __future__ import annotations

import os
from pathlib import Path

from scanner.runtime import process_control
from scanner.runtime.process_control import ProcessControl, WorkerIdentity


class _FakeFunction:
    def __init__(self, result=1):
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *_args):
        self.calls.append(_args)
        return self.result


class _FakeLibrary:
    def __init__(self):
        for name in (
            "CreateJobObjectW", "OpenJobObjectW", "OpenProcess", "AssignProcessToJobObject",
            "IsProcessInJob", "TerminateJobObject", "CloseHandle", "CreateNamedPipeW",
            "ConnectNamedPipe",
            "CreateFileW", "ReadFile", "WriteFile", "FlushFileBuffers", "PeekNamedPipe",
            "DisconnectNamedPipe",
            "LocalFree", "ConvertStringSecurityDescriptorToSecurityDescriptorW",
            "GetCurrentProcess", "OpenProcessToken", "GetTokenInformation",
            "ConvertSidToStringSidW",
        ):
            setattr(self, name, _FakeFunction())


def _wait_for_identity(
    control: ProcessControl,
    scan_id: str,
    pid: int,
    token: str,
    *,
    diagnostic_path: Path | None = None,
):
    identity = control.discover_identity(
        scan_id,
        token,
        launcher_pid=pid,
        process_group=pid if os.name == "posix" else None,
    )
    if identity is not None:
        return identity
    if diagnostic_path is not None and diagnostic_path.exists():
        diagnostic = diagnostic_path.read_text(encoding="ascii").strip()
        if diagnostic:
            raise AssertionError(f"worker control setup failed: {diagnostic}")
    if os.name == "nt":
        # The normal identity API intentionally reports only False. For this
        # native-host test, preserve the final local Win32 connection error
        # without emitting an endpoint name or capability token.
        try:
            connection = process_control.connect_private_endpoint(
                process_control.worker_control_path(scan_id, token), token.encode("utf-8")
            )
        except OSError as error:
            raise AssertionError(
                f"worker control connection failed: {type(error).__name__}: {error}"
            ) from error
        else:
            connection.close()
            raise AssertionError(_proof_response_diagnostic(scan_id, pid, token))
    raise AssertionError("worker control endpoint did not prove identity")


def _proof_response_diagnostic(scan_id: str, expected_pid: int, token: str) -> str:
    """Return only structural proof facts for a failing native Windows test."""

    identity = WorkerIdentity(scan_id, expected_pid, None, token)
    challenge = b"native-proof-diagnostic"
    try:
        connection = process_control.connect_private_endpoint(
            process_control.worker_control_path(scan_id, token), token.encode("utf-8")
        )
        try:
            connection.send_bytes(process_control._control_payload(identity, b"proof", challenge))
            response = connection.recv_bytes()
        finally:
            connection.close()
    except (EOFError, OSError, ValueError) as error:
        return f"worker control proof response error={type(error).__name__}"

    parts = response.split(b" ")
    if len(parts) != 3 or parts[0] != b"proof":
        return f"worker control proof response fields={len(parts)} command_matches=False"
    try:
        actual_pid = int(parts[1])
        actual_proof = process_control.base64.urlsafe_b64decode(parts[2] + b"===")
    except (ValueError, TypeError):
        return "worker control proof response pid_or_proof_invalid"
    proof_valid = process_control.hmac.compare_digest(
        actual_proof, process_control._proof(token, challenge, actual_pid)
    )
    return (
        "worker control proof response "
        f"fields=3 command_matches=True actual_pid={actual_pid} "
        f"expected_pid={expected_pid} proof_valid={proof_valid}"
    )


def _worker_environment(tmp_path: Path) -> dict[str, str]:
    """Create the child roots before advertising them through its environment."""

    home = tmp_path / "home"
    runtime = tmp_path / "temp"
    home.mkdir()
    runtime.mkdir()
    return os.environ | {
        "HOME": str(home),
        "TMPDIR": str(runtime),
        "PYTHONPATH": str(Path(__file__).parents[1]),
        "EDITARR_TEST_CONTROL_DIAGNOSTIC": str(tmp_path / "control-diagnostic.txt"),
    }


