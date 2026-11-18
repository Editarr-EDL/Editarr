#!/usr/bin/env python3
"""Hermetically exercise an already-installed Editarr runtime.

This deliberately black-box verifier never imports ``scanner`` or adds a checkout,
virtualenv, or dependency directory to a frozen program.  The legacy wheel smoke is
retained only for older release documentation; native verification rejects it.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import hmac
import http.cookiejar
import json
import os
import pickle
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from native_build.runtime_layout import NATIVE_TARGETS, helper_name, resolve_executable
from scripts.jellyfin_stand_in import ADMIN, ADMIN_PASSWORD, MOVIE_ID
from scripts.jellyfin_stand_in import serve as serve_jellyfin
from scripts.loopback_ports import available_port as _available_port
from scripts.verifier_survivors import SURVIVING

_ENV_PREFIXES = (
    "EDITARR_",
    "PYTHON",
    "PIP_",
    "VIRTUAL_ENV",
    "CONDA",
    "UV_",
    "HF_",
    "HUGGINGFACE_",
    "TRANSFORMERS_",
    "TORCH_",
    "PYTORCH_",
    "MLX_",
    "XDG_",
)
_SECRET = re.compile(r"(?i)(password|token|secret|cookie|csrf)[^\s,:=]*[=:]\s*[^\s,]+")
_PRIVATE_ARGUMENT = re.compile(r"(?i)(--(?:worker-)?token)(?:=|\s+)\S+")
_MAX_TEXT = 4_000
_WORKER_START_TIMEOUT_SECONDS = 60.0
_WORKER_RECONCILIATION_TIMEOUT_SECONDS = 30.0
_RESTART_TIMEOUT_SECONDS = 15.0
_POLL_INTERVAL_SECONDS = 0.1
_POLL_WAKE = Event()
_SCAN_ID_ARGUMENT = re.compile(r"(?:^|\s)--scan-id(?:=|\s+)([0-9a-f-]{36})(?:\s|$)")
_WORKER_TOKEN_ARGUMENT = re.compile(r"(?:^|\s)--worker-token(?:=|\s+)(\S+)(?:\s|$)")
_MACOS_IDLE_PREVENTER = re.compile(r"(?:/usr/bin/)?caffeinate -i -w (\d+)$")
_NATIVE_TARGETS = NATIVE_TARGETS
_MAX_INVENTORY_BYTES = 64_000
_PRODUCT_VERSION = re.compile(r"[0-9][0-9A-Za-z.!+_-]{0,127}")
_SOURCE_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def _is_windows() -> bool:
    """Keep Windows-only verifier paths explicit and independently testable."""

    return os.name == "nt"


def _accepted_shutdown_codes() -> set[int]:
    # Windows has no SIGKILL attribute and Popen does not use POSIX negative
    # signal return codes there. A successful native Quit must return zero.
    if _is_windows():
        return {0}
    return {0, -signal.SIGTERM, -signal.SIGKILL}


class _WorkerControlUnavailable(RuntimeError):
    """The worker is alive but has not published its private endpoint yet."""


@dataclass
class Evidence:
    target: str
    version: str
    executable: str
    runtime: str
    artifact_sha256: str
    source_revision: str | None = None
    runtime_inventory_sha256: str | None = None
    started: float = field(default_factory=time.monotonic)
    checks: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)

    def output(self) -> dict[str, Any]:
        return {
            "schema": "editarr-installed-artifact-v1",
            "target": self.target,
            "version": self.version,
            "executable": self.executable,
            "runtime": self.runtime,
            "artifact_sha256": self.artifact_sha256,
            "source_revision": self.source_revision,
            "runtime_inventory_sha256": self.runtime_inventory_sha256,
            "duration_seconds": round(time.monotonic() - self.started, 3),
            "roles": self.roles,
            "checks": self.checks,
            "failures": self.failures,
        }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, help="Frozen onedir root or .app bundle")
    parser.add_argument("--editarr", type=Path, help="Legacy installed command")
    parser.add_argument("--target", choices=sorted(_NATIVE_TARGETS))
    parser.add_argument("--dependency-site-packages", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--keep-root", action="store_true")
    parser.add_argument(
        "--recovery",
        action="store_true",
        help="Also prove installed backup restore and offline owner recovery",
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--test-fake-models",
        action="store_true",
        help="Exercise worker cancellation only with a separately marked NATIVE-11 test build",
    )
    args = parser.parse_args()
    if bool(args.runtime) == bool(args.editarr):
        parser.error("provide exactly one of --runtime or --editarr")
    if args.runtime and args.dependency_site_packages:
        parser.error("native verification never accepts dependency site-packages")
    return args


def _redact(value: str) -> str:
    return _PRIVATE_ARGUMENT.sub(r"\1=[REDACTED]", _SECRET.sub("[REDACTED]", value))


def _describe_failure(error: BaseException) -> str:
    """Name each exception in a failure chain with its raising function and notes.

    Evidence must identify the failed step, not only repeat a message that may be
    an arbitrary application log excerpt. ``main`` redacts and bounds the result.
    """
    parts: list[str] = []
    notes: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(parts) < 4:
        seen.add(id(current))
        frames = traceback.extract_tb(current.__traceback__)
        where = f" in {frames[-1].name}:{frames[-1].lineno}" if frames else ""
        parts.append(f"{type(current).__name__}{where}: {current}")
        notes.extend(str(note) for note in getattr(current, "__notes__", ()))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return "\n".join([" <- caused by ".join(parts), *notes])


def _response_summary(status: int, body: bytes) -> str:
    """Describe an unexpected HTTP answer by status, API error code, and body excerpt."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    code = payload.get("code") if isinstance(payload, dict) else None
    excerpt = body[: _MAX_TEXT // 8].decode(errors="replace")
    return f"HTTP {status}; code={code!r}; body={excerpt!r}"


def _log_tail_note(log: Path) -> str:
    """Return a short, redacted runtime log excerpt to attach to a failed check."""
    try:
        tail = log.read_text(errors="replace")[-_MAX_TEXT // 4 :]
    except OSError as error:
        return f"runtime log unavailable: {type(error).__name__}"
    return "runtime log tail: " + _redact(tail)


def _executable(runtime: Path, target: str) -> Path:
    return resolve_executable(runtime, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _runtime_inventory(runtime: Path, expected_target: str | None) -> dict[str, str]:
    """Read identity only from the packaged runtime's build inventory."""

    candidates = [runtime / "editarr-runtime-inventory.json"]
    if runtime.suffix == ".app":
        candidates.extend(
            [
                runtime / "Contents" / "Resources" / "editarr-runtime-inventory.json",
                runtime
                / "Contents"
                / "Resources"
                / "runtime"
                / "Editarr"
                / "editarr-runtime-inventory.json",
                runtime / "Contents" / "MacOS" / "editarr-runtime-inventory.json",
            ]
        )
    found = [path for path in candidates if path.exists() or path.is_symlink()]
    if len(found) != 1:
        raise RuntimeError(
            "packaged runtime must contain exactly one editarr-runtime-inventory.json"
        )
    path = found[0]
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("packaged runtime inventory must be a regular file")
    if path.stat().st_size > _MAX_INVENTORY_BYTES:
        raise RuntimeError("packaged runtime inventory exceeds 64 KiB")
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("packaged runtime inventory is not valid JSON") from error
    if not isinstance(raw, dict) or raw.get("format") != 1:
        raise RuntimeError("packaged runtime inventory format is not supported")
    version = raw.get("version")
    if not isinstance(version, str) or _PRODUCT_VERSION.fullmatch(version) is None:
        raise RuntimeError("packaged runtime inventory has an invalid version")
    target = raw.get("target")
    if target not in _NATIVE_TARGETS:
        raise RuntimeError("packaged runtime inventory has an invalid target")
    if expected_target is not None and target != expected_target:
        raise RuntimeError(
            f"requested target {expected_target!r} does not match packaged target {target!r}"
        )
    source_revision = raw.get("source_revision")
    if (
        not isinstance(source_revision, str)
        or _SOURCE_REVISION.fullmatch(source_revision) is None
    ):
        raise RuntimeError("packaged runtime inventory has an invalid source revision")
    return {
        "target": target,
        "version": version,
        "source_revision": source_revision,
        "inventory_path": path.relative_to(runtime).as_posix(),
        "inventory_sha256": _sha256(path),
    }


def _embedded_runtime_root(runtime: Path) -> Path:
    """Return the frozen runtime root inside an app, otherwise ``runtime`` itself."""

    if runtime.suffix == ".app":
        return runtime / "Contents" / "Resources" / "runtime" / "Editarr"
    return runtime


def _bundled_helper(runtime: Path, name: str, target: str) -> Path:
    """Locate a frozen helper beneath either a raw runtime or an application bundle."""

    return _embedded_runtime_root(runtime) / "_internal" / "helpers" / helper_name(name, target)


def _program_snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    return {
        str(path.relative_to(root)): (stat.st_size, stat.st_mtime_ns, stat.st_mode & 0o777)
        for path in root.rglob("*")
        if path.is_file()
        for stat in (path.stat(),)
    }


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if not path.is_symlink():
            try:
                path.chmod(0o555 if path.is_dir() else path.stat().st_mode & 0o555)
            except OSError as error:
                raise RuntimeError(
                    f"cannot mark copied runtime read-only: {path.name}: {error}"
                ) from error
    root.chmod(0o555)


def _remove_root(root: Path) -> str | None:
    """Remove scratch, including a runtime copy ``_make_read_only`` protected.

    Returns why the root is still present instead of raising, so a cleanup error
    never replaces the verification result.
    """
    error: OSError | None = None
    try:
        for path in [root, *root.rglob("*")]:
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | (0o700 if path.is_dir() else 0o200))
    except OSError as caught:
        error = caught
    try:
        shutil.rmtree(root)
    except OSError as caught:
        error = error or caught
    if not root.exists():
        return None
    return f"{type(error).__name__}: {error}" if error else "scratch root still exists"


def _clean_scratch(root: Path, *, keep: bool) -> dict[str, Any]:
    """Remove scratch unless asked to keep it; report anything left behind."""
    if SURVIVING:
        reason = f"owned processes {SURVIVING} survived cleanup"
    elif keep:
        reason = "--keep-root"
    else:
        reason = _remove_root(root)
        if reason is None:
            return {"removed": True}
    retained = 0
    for path in root.rglob("*"):
        try:
            retained += path.lstat().st_size if path.is_file() else 0
        except OSError:
            continue
    runtime_copy = any(root.glob("Editarr verified*runtime*"))
    print(
        f"kept {root}: {reason}; {retained} bytes retained"
        + (", including the runtime copy" if runtime_copy else ""),
        file=sys.stderr,
    )
    return {
        "removed": False,
        "root": str(root),
        "reason": _redact(reason)[:_MAX_TEXT],
        "retained_bytes": retained,
        "runtime_copy_retained": runtime_copy,
    }


def _environment(
    install: Path, dependencies: Path | None, data: Path, *, native: bool = False
) -> dict[str, str]:
    """Return an isolated environment; ``dependencies`` is legacy-wheel-only."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(_ENV_PREFIXES)
    }
    root = data.parent
    # Frozen Windows programs resolve their private DLLs beside the executable, but
    # their ordinary OS commands still require System32.  Keep that system-only
    # baseline rather than inheriting an external Python, Node, or checkout PATH.
    safe_path = "/usr/bin:/bin"
    if _is_windows():
        system_root = os.environ.get("SystemRoot", r"C:\\Windows")
        safe_path = str(Path(system_root) / "System32")
    environment.update(
        {
            "HOME": str(root / "home"),
            "USERPROFILE": str(root / "home"),
            "LOCALAPPDATA": str(root / "local-app-data"),
            "TMPDIR": str(root / "tmp"),
            "TEMP": str(root / "tmp"),
            "TMP": str(root / "tmp"),
            "XDG_CACHE_HOME": str(root / "xdg-cache"),
            "XDG_CONFIG_HOME": str(root / "xdg-config"),
            "XDG_DATA_HOME": str(root / "xdg-data"),
            "HF_HOME": str(root / "hf"),
            "HUGGINGFACE_HUB_CACHE": str(root / "hf" / "hub"),
            "TRANSFORMERS_CACHE": str(root / "transformers"),
            "TORCH_HOME": str(root / "torch"),
            "MLX_CACHE_DIR": str(root / "mlx"),
            "EDITARR_DATA_DIR": str(data),
            "EDITARR_DATABASE_URL": f"sqlite:///{data / 'scanner.db'}",
            "EDITARR_FRAMES_DIR": str(root / "frames"),
            "EDITARR_EXPORTS_DIR": str(root / "exports"),
            "EDITARR_QWEN_REPLAYS_DIR": str(root / "qwen-replays"),
            # The verifier talks directly to its isolated local HTTP listener.
            # Production HTTPS/proxy coverage remains in the browser fixture.
            "EDITARR_AUTH_COOKIE_SECURE": "0",
            "BROWSER": "true",
            "PATH": safe_path,
        }
    )
    for value in (root / "home", root / "tmp"):
        value.mkdir(parents=True, exist_ok=True)
    if not native:
        environment["PATH"] = f"{install / 'bin'}:/usr/bin:/bin"
    if dependencies is not None:
        environment["PYTHONPATH"] = str(dependencies)
    else:
        environment.pop("PYTHONPATH", None)
    return environment


def _start(executable: Path, port: int | None, cwd: Path, environment: dict[str, str]):
    log = cwd / f"Editarr-{time.monotonic_ns()}.log"
    command = [str(executable), "--no-browser"] + ([] if port is None else ["--port", str(port)])
    output = log.open("wb")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    output.close()
    return process, log


class _WindowsPipeClient:
    """Minimal client for Editarr's authenticated, framed named-pipe protocol.

    The verifier intentionally cannot import ``scanner.runtime.process_control`` from the
    checkout.  This implements only the already-public wire contract used by the
    frozen launcher and worker: an HMAC connection handshake followed by
    length-prefixed pickle or byte payloads.
    """

    def __init__(self, handle: int, kernel32: object) -> None:
        self.handle = handle
        self.kernel32 = kernel32

    def _read_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            buffer = ctypes.create_string_buffer(length - len(chunks))
            received = ctypes.c_ulong()
            if not self.kernel32.ReadFile(
                self.handle, buffer, len(buffer), ctypes.byref(received), None
            ):
                raise OSError(ctypes.get_last_error(), "could not read Editarr named pipe")
            if not received.value:
                raise EOFError("Editarr named pipe closed")
            chunks.extend(buffer.raw[: received.value])
        return bytes(chunks)

    def send_bytes(self, payload: bytes) -> None:
        message = struct.pack("!I", len(payload)) + payload
        written = ctypes.c_ulong()
        buffer = ctypes.create_string_buffer(message)
        if not self.kernel32.WriteFile(
            self.handle, buffer, len(message), ctypes.byref(written), None
        ) or written.value != len(message):
            raise OSError(ctypes.get_last_error(), "could not write Editarr named pipe")

    def recv_bytes(self) -> bytes:
        length = struct.unpack("!I", self._read_exact(4))[0]
        if length > 16 * 1024 * 1024:
            raise ValueError("Editarr named-pipe message is too large")
        return self._read_exact(length)

    def send(self, value: object) -> None:
        self.send_bytes(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))

    def recv(self) -> object:
        return pickle.loads(self.recv_bytes())

    def close(self) -> None:
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = 0


def _windows_kernel32():
    """Return a pointer-safe kernel32 binding only on the Windows verifier host."""

    if not _is_windows():
        raise RuntimeError("Windows named-pipe client is unavailable on this host")
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    kernel32.WaitNamedPipeW.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _connect_windows_pipe(address: str, token: bytes) -> _WindowsPipeClient:
    """Connect to one same-user Editarr named pipe and prove its capability."""

    kernel32 = _windows_kernel32()
    invalid_handle = ctypes.c_void_p(-1).value
    deadline = time.monotonic() + 2.0
    last_error = 0
    while True:
        handle = kernel32.CreateFileW(address, 0xC0000000, 0, None, 3, 0, None)
        if handle and handle != invalid_handle:
            connection = _WindowsPipeClient(handle, kernel32)
            try:
                connection.send_bytes(hmac.new(token, b"connect", hashlib.sha256).digest())
                if connection.recv_bytes() != b"ok":
                    raise PermissionError("Editarr named-pipe authentication failed")
                return connection
            except BaseException:
                connection.close()
                raise
        last_error = ctypes.get_last_error()
        if time.monotonic() >= deadline:
            raise OSError(last_error, "could not connect to Editarr named pipe")
        kernel32.WaitNamedPipeW(address, 50)


def _stop_windows(process: subprocess.Popen[bytes]) -> None:
    """Force-clean a verifier-owned Windows process tree without POSIX signals."""

    if process.poll() is not None:
        return
    # The frozen controller owns its HTTP child.  /T is needed only on failure
    # cleanup, and targets the exact verifier-created PID rather than a name.
    completed = subprocess.run(
        ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired as error:
        SURVIVING.append(process.pid)
        raise RuntimeError(
            "verifier-owned Windows process tree survived taskkill "
            f"(exit {completed.returncode})"
        ) from error


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Stop the process group created by ``_start``, including an app-wrapper child.

    The macOS AppKit executable can wait on the embedded frozen runtime.  That
    Foundation-launched child may use a separate process group, so keep the
    immediately observed descendant PIDs as well as the verifier-created group.
    A wrapper exit is never proof that the copied application is gone.
    """
    if _is_windows():
        _stop_windows(process)
        return

    owned_pids = {pid for pid, _parent, _command in _descendant_rows(process.pid)}

    def surviving() -> set[int]:
        alive: set[int] = set()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            pass
        else:
            alive.add(process.pid)
        for pid in owned_pids - {process.pid}:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            else:
                alive.add(pid)
        return alive

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    for pid in owned_pids - {process.pid}:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue

    if process.poll() is None:
        try:
            process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            pass

    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if not surviving():
            return
        time.sleep(0.05)

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for pid in owned_pids - {process.pid}:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not surviving():
            return
        time.sleep(0.05)
    SURVIVING.append(process.pid)
    raise RuntimeError("verifier process group survived forced cleanup")


def _request(
    url: str,
    *,
    opener=None,
    data: dict[str, Any] | None = None,
    csrf: str | None = None,
    method: str | None = None,
    timeout: float = 15,
):
    parsed = urlsplit(url)
    headers = (
        {"Origin": f"{parsed.scheme}://{parsed.netloc}"} if "/api/" in url else {}
    )
    if csrf:
        headers["X-CSRF-Token"] = csrf
    request = Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers={**headers, **({"Content-Type": "application/json"} if data is not None else {})},
        method=method or ("POST" if data is not None else "GET"),
    )
    target = opener.open if opener is not None else urlopen
    try:
        # First use of media metadata bindings in a frozen runtime can take a few
        # seconds. Keep the verifier bounded while not treating normal cold-start
        # queue admission as a network failure.
        with target(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _wait_ready(origin: str, process: subprocess.Popen[bytes]):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"runtime exited before readiness ({process.returncode})")
        try:
            response = _request(origin + "/api/auth/status")
            if response[0] == 200:
                return response
        except (URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("runtime readiness timed out")


def _asset_path(html: str) -> str:
    match = re.search(r'(?:src|href)="(?:\./)?(assets/[^\"]+\.(?:js|css))"', html)
    if match is None:
        raise RuntimeError("installed entry HTML has no bundled JS/CSS reference")
    return match.group(1)


def _process_rows() -> list[tuple[int, int, str]]:
    """Return the host process table without putting private argv values in evidence."""
    if _is_windows():
        command = (
            "$ErrorActionPreference = 'Stop'; "
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
            "Get-CimInstance Win32_Process | ForEach-Object { "
            "[PSCustomObject]@{ProcessId=$_.ProcessId;ParentProcessId=$_.ParentProcessId;"
            "CommandLine=$_.CommandLine} } | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            encoding="utf-8",
            check=True,
            timeout=15,
        )
        try:
            raw = json.loads(result.stdout.lstrip("\ufeff"))
        except json.JSONDecodeError as error:
            raise RuntimeError("could not decode Windows process inventory") from error
        records = raw if isinstance(raw, list) else [raw]
        return [
            (record["ProcessId"], record["ParentProcessId"], record["CommandLine"])
            for record in records
            if isinstance(record, dict)
            and isinstance(record.get("ProcessId"), int)
            and isinstance(record.get("ParentProcessId"), int)
            and isinstance(record.get("CommandLine"), str)
        ]
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, check=True
    )
    return [
        (int(parts[0]), int(parts[1]), parts[2])
        for line in result.stdout.splitlines()
        if len(parts := line.strip().split(None, 2)) == 3
        and parts[0].isdigit()
        and parts[1].isdigit()
    ]


def _descendant_rows(pid: int) -> list[tuple[int, int, str]]:
    rows = _process_rows()
    pending, selected = {pid}, []
    while pending:
        parent = pending.pop()
        for row in rows:
            if row[1] == parent and row not in selected:
                selected.append(row)
                pending.add(row[0])
    selected.append(next((row for row in rows if row[0] == pid), (pid, 0, "")))
    return sorted(selected)


def _process_role(command: str) -> str:
    if "--internal-role http-child" in command:
        return "http-child"
    if "--internal-role scan-worker" in command:
        return "scan-worker"
    if _MACOS_IDLE_PREVENTER.fullmatch(command):
        return "macos-idle-preventer"
    return "controller"


def _descendants(pid: int) -> list[dict[str, Any]]:
    return [
        {
            "pid": child,
            "parent_pid": parent,
            "role": _process_role(command),
            "command": _redact(command)[:_MAX_TEXT],
        }
        for child, parent, command in _descendant_rows(pid)
    ]


def _assert_packaged_processes(roles: list[dict[str, Any]], runtime: Path) -> None:
    forbidden = ("python", "node", "vite", "/.venv/", "/scanner/")
    # macOS exposes the same temporary directory through both ``/var`` and its
    # physical ``/private/var`` spelling. ``ps`` chooses the latter even when the
    # verifier launched the immutable copied runtime through the former.
    runtime_origins = {str(runtime), str(runtime.resolve()), os.path.realpath(runtime)}
    if str(runtime).startswith("/var/"):
        runtime_origins.add("/private" + str(runtime))
    # APFS normalizes Unicode filenames while ``ps`` may print a different Unicode
    # normalization form. Compare canonical path spellings, not their byte form.
    normalized_origins = {
        unicodedata.normalize("NFC", origin) for origin in runtime_origins
    }
    role_by_pid = {role.get("pid"): role for role in roles}
    for role in roles:
        command = str(role["command"])
        idle_preventer = _MACOS_IDLE_PREVENTER.fullmatch(command)
        if idle_preventer:
            parent = role_by_pid.get(role.get("parent_pid"))
            if (
                sys.platform == "darwin"
                and parent
                and parent.get("role") == "scan-worker"
                and idle_preventer.group(1) == str(parent.get("pid"))
            ):
                continue
            raise RuntimeError("unexpected macOS idle-preventer process")
        normalized_command = unicodedata.normalize("NFC", command)
        executable_text = command.split(" --", 1)[0]
        try:
            executable_parent_matches = os.path.samefile(
                Path(executable_text).parent, runtime
            )
        except OSError:
            executable_parent_matches = False
        origin_matches = any(origin in normalized_command for origin in normalized_origins)
        if command and not (origin_matches or executable_parent_matches):
            raise RuntimeError("runtime child did not originate in copied program directory")
        if any(value in command.lower() for value in forbidden):
            raise RuntimeError("runtime child used an external language runtime or checkout")


def _record_roles(evidence: Evidence, roles: list[dict[str, Any]]) -> None:
    """Retain each observed packaged process even after a short-lived worker exits."""
    existing = {(role["pid"], role["command"]) for role in evidence.roles}
    evidence.roles.extend(
        role for role in roles if (role["pid"], role["command"]) not in existing
    )


def _worker_control_endpoint(root: Path, scan_id: str, token: str) -> Path:
    """Reproduce the documented POSIX endpoint derivation without importing Editarr."""
    socket_root = root / "tmp" / "editarr-worker-control"
    if len(os.fsencode(str(socket_root))) > 55:
        socket_root = Path("/tmp") / f"editarr-ipc-{os.getuid()}" / "editarr-worker-control"
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    return socket_root / f"w-{scan_id[:8]}-{token_digest}.sock"


def _native_instance_endpoint(data: Path, token: str) -> Path | str:
    """Reproduce the target launcher endpoint without importing the application."""
    token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()[:32]
    if _is_windows():
        return rf"\\.\pipe\EditarrLauncher-{token_digest}"
    socket_root = data.parent / "tmp" / "editarr-launcher-control"
    if len(os.fsencode(str(socket_root))) > 55:
        socket_root = Path("/tmp") / f"editarr-ipc-{os.getuid()}" / "editarr-launcher-control"
    return socket_root / f"instance-{token_digest}.sock"


def _connect_native_launcher(endpoint: str, token: str):
    """Open the target launcher protocol using only the standard library."""
    if _is_windows():
        return _connect_windows_pipe(endpoint, token.encode("ascii"))
    from multiprocessing.connection import Client

    return Client(endpoint, family="AF_UNIX", authkey=token.encode("ascii"))


def _request_native_quit(data: Path, controller_pids: set[int]) -> dict[str, Any]:
    """Request an authenticated full application Quit through launcher IPC.

    This is deliberately implemented with standard-library framing only; importing
    ``scanner.runtime.native_lifecycle`` here would turn the installed-artifact check into a
    checkout import. POSIX uses ``multiprocessing.connection`` over AF_UNIX; Windows
    uses the same authenticated framing over the token-derived named pipe.
    """
    try:
        metadata = json.loads((data / "native-instance" / "instance.json").read_bytes())
        token = metadata["token"]
        endpoint = metadata["endpoint"]
        owner_pid = metadata["pid"]
        ready = metadata["ready"]
    except (KeyError, OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("native launcher state was unavailable for full Quit") from error
    if (
        not isinstance(token, str)
        or not isinstance(endpoint, str)
        or not isinstance(owner_pid, int)
        or isinstance(owner_pid, bool)
        or owner_pid not in controller_pids
        or ready is not True
        or endpoint != str(_native_instance_endpoint(data, token))
    ):
        raise RuntimeError("native launcher state did not prove the running controller")
    try:
        connection = _connect_native_launcher(endpoint, token)
        try:
            connection.send("quit")
            response = connection.recv()
        finally:
            connection.close()
    except (OSError, EOFError, UnicodeEncodeError, ValueError) as error:
        raise RuntimeError("authenticated native launcher Quit request failed") from error
    if (
        not isinstance(response, dict)
        or response.get("ok") is not True
        or response.get("pid") != owner_pid
    ):
        raise RuntimeError("native launcher rejected the full Quit request")
    return {
        "transport": "windows-named-pipe" if _is_windows() else "unix-domain-socket",
        "authenticated": True,
        "pid": owner_pid,
    }


def _prove_worker_control(
    root: Path, worker_pid: int, scan_id: str, token: str
) -> dict[str, Any]:
    """Authenticate a live POSIX worker endpoint and bind it to its packaged PID."""
    if os.name != "posix":
        raise RuntimeError("test fake-worker control proof currently requires POSIX")
    endpoint = _worker_control_endpoint(root, scan_id, token)
    try:
        mode = endpoint.stat().st_mode & 0o777
    except OSError as error:
        raise _WorkerControlUnavailable(
            "test-only fake worker control endpoint is unavailable"
        ) from error
    if mode != 0o600:
        raise RuntimeError("test-only fake worker control endpoint is not owner-private")
    challenge = os.urandom(24)
    request_proof = hmac.new(
        token.encode("utf-8"), b"proof:" + challenge, hashlib.sha256
    ).digest()
    payload = (
        b"proof "
        + base64.urlsafe_b64encode(challenge)
        + b" "
        + base64.urlsafe_b64encode(request_proof)
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(endpoint))
            client.sendall(payload)
            response = client.recv(512)
    except OSError as error:
        # The queue transitions to ``scanning`` as soon as it reserves its worker.
        # A frozen worker can still be importing its pipeline at that point, so its
        # endpoint may disappear between stat and connect.  The caller retries this
        # one transient condition while the packaged child remains alive.
        raise _WorkerControlUnavailable(
            "test-only fake worker control proof is unavailable"
        ) from error
    parts = response.split(b" ")
    if len(parts) != 3 or parts[0] != b"proof":
        raise RuntimeError("test-only fake worker control endpoint returned an invalid proof")
    try:
        proof_pid = int(parts[1])
        supplied = base64.urlsafe_b64decode(parts[2] + b"===")
    except ValueError as error:
        raise RuntimeError(
            "test-only fake worker control endpoint returned malformed data"
        ) from error
    expected = hmac.new(
        token.encode("utf-8"), challenge + b":" + str(proof_pid).encode("ascii"), hashlib.sha256
    ).digest()
    if proof_pid != worker_pid or not hmac.compare_digest(supplied, expected):
        raise RuntimeError("test-only fake worker control proof did not match its packaged process")
    return {
        "transport": "unix-domain-socket",
        "authenticated": True,
        "pid": proof_pid,
        "endpoint_mode": oct(mode),
    }


def _live_packaged_worker(
    root: Path, controller_pid: int, runtime: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Find one frozen worker and wait briefly for its authenticated endpoint.

    The scheduler writes the observable ``scanning`` state before the child has
    completed its frozen imports. Do not mistake that normal interval for a worker
    failure, but never paper over a malformed command, an unsafe socket, or a
    disappeared child after it was observed.
    """
    deadline = time.monotonic() + _WORKER_START_TIMEOUT_SECONDS
    saw_worker = False
    last_unavailable: str | None = None
    while True:
        raw_roles = _descendant_rows(controller_pid)
        roles = [
            {
                "pid": pid,
                "parent_pid": parent,
                "role": _process_role(command),
                "command": _redact(command)[:_MAX_TEXT],
            }
            for pid, parent, command in raw_roles
        ]
        _assert_packaged_processes(roles, runtime)
        workers = [
            (pid, command)
            for pid, _parent, command in raw_roles
            if _process_role(command) == "scan-worker"
        ]
        if len(workers) > 1:
            raise RuntimeError("test-only fake worker process was observed more than once")
        if len(workers) == 1:
            saw_worker = True
            worker_pid, command = workers[0]
            scan_match = _SCAN_ID_ARGUMENT.search(command)
            token_match = _WORKER_TOKEN_ARGUMENT.search(command)
            if scan_match is None or token_match is None:
                raise RuntimeError(
                    "packaged fake worker command lacked its private control identity"
                )
            try:
                return roles, _prove_worker_control(
                    root, worker_pid, scan_match.group(1), token_match.group(1)
                )
            except _WorkerControlUnavailable as error:
                last_unavailable = str(error)
        elif saw_worker:
            raise RuntimeError("test-only fake worker exited before its control endpoint was ready")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            detail = f" ({last_unavailable})" if last_unavailable else ""
            raise RuntimeError(
                "test-only fake worker did not establish an authenticated control endpoint "
                f"within {_WORKER_START_TIMEOUT_SECONDS:g}s{detail}"
            )
        _POLL_WAKE.wait(min(_POLL_INTERVAL_SECONDS, remaining))


# The fake-worker scans queue the stand-in's movie, whose file `_worker_smoke` generates.
_FAKE_MEDIA_NAME = "fake-model-fixture.mp4"
_FAKE_SCAN = {"jellyfin_item_id": MOVIE_ID, "scan_audio": False, "scan_video": True}


def _wait_for_scanning(
    origin: str, opener: Any, process: subprocess.Popen[bytes], queue_id: int
) -> dict[str, Any]:
    """Observe a frozen worker through the queue API, bounded for cold imports."""
    deadline = time.monotonic() + _WORKER_START_TIMEOUT_SECONDS
    last_queue_status: int | None = None
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"runtime exited while starting fake worker ({process.returncode})")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                "test-only fake worker did not become active within "
                f"{_WORKER_START_TIMEOUT_SECONDS:g}s (queue HTTP {last_queue_status})"
            )
        try:
            status, _headers, queue_body = _request(
                origin + "/api/scans/queue", opener=opener, timeout=min(2.0, remaining)
            )
        except (OSError, TimeoutError, URLError) as error:
            raise RuntimeError("test-only fake worker queue control request failed") from error
        last_queue_status = status
        if status != 200:
            raise RuntimeError(f"test-only fake worker queue control returned HTTP {status}")
        try:
            queue = json.loads(queue_body)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                "test-only fake worker queue control returned invalid JSON"
            ) from error
        active_item = next((item for item in queue if item.get("queue_id") == queue_id), None)
        if active_item and active_item.get("status") == "scanning":
            return active_item
        _POLL_WAKE.wait(min(_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))


def _worker_smoke(
    origin: str,
    opener: Any,
    csrf: str,
    root: Path,
    runtime: Path,
    controller: subprocess.Popen[bytes],
    evidence: Evidence,
) -> None:
    source = root / _FAKE_MEDIA_NAME
    # The stand-in's movie points at this file and scan creation requires it to
    # be readable.  Generate one valid, tiny video through the copied runtime's
    # bundled helper, not an external system tool, so the worker test remains
    # fully hermetic.
    helper = _bundled_helper(runtime, "ffmpeg", evidence.target)
    if not helper.is_file():
        raise RuntimeError("marked test runtime lacks its bundled ffmpeg helper")
    try:
        subprocess.run(
            [
                str(helper),
                "-f",
                "lavfi",
                "-i",
                "color=size=16x16:rate=1:color=black",
                "-t",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-y",
                str(source),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("could not create the test-only fake-worker media fixture") from error
    try:
        queued_status, _headers, queued_body = _request(
            origin + "/api/scans", opener=opener, csrf=csrf, data=_FAKE_SCAN
        )
    except (OSError, TimeoutError) as error:
        raise RuntimeError("test-only fake worker queue request timed out") from error
    queued = json.loads(queued_body)
    queue_id = queued.get("queue_id")
    if queued_status != 201 or not isinstance(queue_id, int):
        raise RuntimeError("test-only fake worker did not enqueue")
    active_item = _wait_for_scanning(origin, opener, controller, queue_id)
    worker_roles, worker_control = _live_packaged_worker(root, controller.pid, runtime)
    _record_roles(evidence, worker_roles)
    cancelled_status, _headers, cancelled_body = _request(
        origin + f"/api/scans/queue/{queue_id}", opener=opener, csrf=csrf, method="DELETE"
    )
    if cancelled_status != 200 or not json.loads(cancelled_body).get("cancelled"):
        raise RuntimeError(f"test-only fake worker cancellation failed (HTTP {cancelled_status})")
    reconciled = False
    deadline = time.monotonic() + _WORKER_RECONCILIATION_TIMEOUT_SECONDS
    while True:
        if controller.poll() is not None:
            raise RuntimeError(
                f"runtime exited while reconciling fake worker ({controller.returncode})"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            status, _headers, queue_body = _request(
                origin + "/api/scans/queue", opener=opener, timeout=min(2.0, remaining)
            )
        except (OSError, TimeoutError, URLError) as error:
            raise RuntimeError(
                "test-only fake worker reconciliation control request failed"
            ) from error
        if status == 200 and not any(
            item.get("queue_id") == queue_id for item in json.loads(queue_body)
        ):
            reconciled = True
            break
        if status != 200:
            raise RuntimeError(
                f"test-only fake worker reconciliation control returned HTTP {status}"
            )
        _POLL_WAKE.wait(min(_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    if not reconciled:
        raise RuntimeError("test-only fake worker cancellation did not reconcile the queue")
    evidence.checks["test_fake_worker_cancel_reconcile"] = {
        "enqueue": queued_status,
        "cancel": cancelled_status,
        "cancelled": True,
        "active_status": active_item["status"],
        "reconciled": reconciled,
        "process_roles": worker_roles,
        "worker_control": worker_control,
    }


def _login(origin: str) -> tuple[Any, str]:
    """Sign the Jellyfin owner in again after a deployment-scope transition.

    A startup account sync can supersede the login's Jellyfin read. Retry its
    temporary refusal and connection failures up to the restart deadline.
    """
    deadline = time.monotonic() + _RESTART_TIMEOUT_SECONDS
    while True:
        opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        try:
            status, _headers, body = _request(
                origin + "/api/auth/jellyfin/login",
                opener=opener,
                data={"username": ADMIN, "password": ADMIN_PASSWORD},
                timeout=max(0.01, deadline - time.monotonic()),
            )
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if not isinstance(payload, dict):
                payload = {}
            if status != 503 or payload.get("code") != "jellyfin_unavailable":
                break
            if time.monotonic() >= deadline:
                break
        except (URLError, ConnectionError, TimeoutError) as error:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"sign-in after restart could not connect: {error}") from error
        _POLL_WAKE.wait(_POLL_INTERVAL_SECONDS)
    csrf = payload.get("csrf_token")
    if status != 200 or not isinstance(csrf, str) or not csrf:
        raise RuntimeError(
            "restart transition did not allow a fresh authenticated session "
            f"({_response_summary(status, body)})"
        )
    return opener, csrf


def _wait_restart_outcome(
    origin: str,
    opener: Any,
    process: subprocess.Popen[bytes],
    target_generation: int,
    outcome: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + _RESTART_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"runtime exited during restart ({process.returncode})")
        try:
            status, _headers, body = _request(origin + "/api/system/restart", opener=opener)
            payload = json.loads(body)
            if (
                status == 200
                and payload.get("generation", 0) >= target_generation
                and payload.get("outcome") == outcome
            ):
                return payload
        except (OSError, TimeoutError, URLError, json.JSONDecodeError):
            pass
        _POLL_WAKE.wait(_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"restart did not reach generation {target_generation} with {outcome}")


def _request_restart(
    origin: str, opener: Any, csrf: str, process: subprocess.Popen[bytes]
) -> dict[str, Any]:
    status, _headers, body = _request(
        origin + "/api/system/restart", opener=opener, csrf=csrf, data={}
    )
    payload = json.loads(body)
    target_generation = payload.get("target_generation")
    if status != 202 or not isinstance(target_generation, int):
        raise RuntimeError(
            f"restart request was not accepted by the packaged controller (HTTP {status})"
        )
    return payload


def _http_children(process: subprocess.Popen[bytes]) -> set[int]:
    return {
        pid
        for pid, _parent, command in _descendant_rows(process.pid)
        if _process_role(command) == "http-child"
    }


def _restart_and_wait(
    origin: str,
    opener: Any,
    csrf: str,
    process: subprocess.Popen[bytes],
    ready_origin: str,
) -> dict[str, Any]:
    """Request a restart and wait for readiness from a listener the restart started.

    The controller stops the serving HTTP child only after the 202 response, so a
    ready answer before that child exits can come from the old listener.
    """
    previous = _http_children(process)
    if not previous:
        raise RuntimeError("no packaged HTTP child was serving before the restart")
    accepted = _request_restart(origin, opener, csrf, process)
    deadline = time.monotonic() + _RESTART_TIMEOUT_SECONDS
    while previous & _http_children(process):
        if process.poll() is not None:
            raise RuntimeError(f"runtime exited during restart ({process.returncode})")
        if time.monotonic() >= deadline:
            raise RuntimeError("the previous HTTP listener did not stop after the restart request")
        _POLL_WAKE.wait(_POLL_INTERVAL_SECONDS)
    _wait_ready(ready_origin, process)
    return accepted


def _deployment_lifecycle_smoke(
    origin: str,
    opener: Any,
    csrf: str,
    process: subprocess.Popen[bytes],
    data: Path,
    evidence: Evidence,
) -> tuple[str, str, Any, str]:
    """Prove prefixed routing, persisted-port restart, and occupied-bind recovery."""
    prefix = "/native-verify"
    prefixed_origin = origin + prefix
    status, _headers, body = _request(
        origin + "/api/settings/deployment",
        opener=opener,
        csrf=csrf,
        data={"base_url": prefixed_origin},
        method="PUT",
    )
    prefix_saved = json.loads(body)
    if status != 200 or not prefix_saved.get("restart_required"):
        raise RuntimeError(
            "Base URL prefix was not saved for restart "
            f"(HTTP {status}; restart_required={prefix_saved.get('restart_required')!r})"
        )
    accepted = _restart_and_wait(origin, opener, csrf, process, prefixed_origin)
    opener, csrf = _login(prefixed_origin)
    restarted = _wait_restart_outcome(
        prefixed_origin, opener, process, accepted["target_generation"], "applied"
    )
    html_status, _headers, html = _request(prefixed_origin + "/")
    asset_status, _headers, _asset = _request(
        prefixed_origin + "/" + _asset_path(html.decode())
    )
    root_api_status, _headers, _body = _request(origin + "/api/auth/status")
    if html_status != 200 or asset_status != 200 or root_api_status != 404:
        raise RuntimeError("Base URL prefix did not mount packaged UI and API exclusively")
    evidence.checks["base_url_prefix"] = {
        "prefix": prefix,
        "restart_generation": restarted["generation"],
        "html": html_status,
        "asset": asset_status,
        "outside_prefix": root_api_status,
    }

    saved_port = _available_port()
    saved_origin = f"http://localhost:{saved_port}{prefix}"
    status, _headers, body = _request(
        prefixed_origin + "/api/settings/deployment",
        opener=opener,
        csrf=csrf,
        data={"base_url": saved_origin, "port": saved_port},
        method="PUT",
    )
    if status != 200 or not json.loads(body).get("restart_required"):
        raise RuntimeError("saved-port change was not staged")
    accepted = _restart_and_wait(prefixed_origin, opener, csrf, process, saved_origin)
    opener, csrf = _login(saved_origin)
    restarted = _wait_restart_outcome(
        saved_origin, opener, process, accepted["target_generation"], "applied"
    )
    saved = json.loads((data / "deployment.json").read_text())
    if saved.get("port") != saved_port or saved.get("base_url") != saved_origin:
        raise RuntimeError("saved port was not retained by the packaged runtime")
    evidence.checks["saved_port_restart"] = {
        "port": saved_port,
        "restart_generation": restarted["generation"],
        "ready": True,
    }

    occupied_port = _available_port()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", occupied_port))
        occupied.listen()
        pending_origin = f"http://localhost:{occupied_port}/pending"
        status, _headers, body = _request(
            saved_origin + "/api/settings/deployment",
            opener=opener,
            csrf=csrf,
            data={"base_url": pending_origin, "port": occupied_port},
            method="PUT",
        )
        if status != 200 or not json.loads(body).get("restart_required"):
            raise RuntimeError("occupied-port recovery change was not staged")
        accepted = _restart_and_wait(saved_origin, opener, csrf, process, saved_origin)
        opener, csrf = _login(saved_origin)
        recovered = _wait_restart_outcome(
            saved_origin, opener, process, accepted["target_generation"], "recovered"
        )
    status, _headers, body = _request(saved_origin + "/api/settings/deployment", opener=opener)
    if status != 200 or not json.loads(body).get("restart_required"):
        raise RuntimeError("failed bind did not retain pending deployment changes")
    evidence.checks["failed_bind_recovery"] = {
        "occupied_port": occupied_port,
        "restart_generation": recovered["generation"],
        "outcome": recovered["outcome"],
        "detail": "pending_changes_retained",
        "old_listener_ready": True,
    }
    # Recovery intentionally keeps the prior listener alive while the desired
    # configuration remains pending.  Once that listener is fully quit, the
    # occupied test port is released and a cold controller must start at the
    # pending origin instead.  Return both origins so the caller verifies each
    # contract at the correct point in the transition.
    return saved_origin, pending_origin, opener, csrf


def _native_smoke(
    executable: Path,
    runtime: Path,
    root: Path,
    evidence: Evidence,
    *,
    jellyfin: str,
    test_fake_models: bool,
) -> None:
    data = root / "runtime data"
    data.mkdir()
    port = _available_port()
    (data / "deployment.json").write_text(json.dumps({"version": 1, "port": port}))
    environment = _environment(runtime, None, data, native=True)
    if test_fake_models:
        marker = _embedded_runtime_root(runtime) / "_internal" / "native-test-fake-models.marker"
        if not marker.is_file():
            raise RuntimeError("test-fake-models requires the separate marked frozen test build")
        environment["EDITARR_TEST_FAKE_MODELS"] = "1"
    before = _program_snapshot(runtime)
    process, log = _start(executable, None, root, environment)
    failure: BaseException | None = None
    try:
        # The default deployment snapshot's canonical public origin is localhost;
        # use that exact host so setup also proves Origin validation rather than
        # accidentally testing a mismatched loopback alias.
        origin = f"http://localhost:{port}"
        status, _headers, body = _wait_ready(origin, process)
        initial = json.loads(body)
        html_status, _h, html_body = _request(origin + "/")
        asset_status, asset_headers, asset_body = _request(
            origin + "/" + _asset_path(html_body.decode())
        )
        protected_status, _h, _b = _request(origin + "/api/models")
        evidence.checks["first_run_readiness"] = {"status": status, "state": initial.get("state")}
        evidence.checks["bundled_ui"] = {
            "html": html_status,
            "asset": asset_status,
            "asset_bytes": len(asset_body),
            "cache_control": asset_headers.get("Cache-Control"),
        }
        evidence.checks["protected_before_setup"] = protected_status
        jar = http.cookiejar.CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        setup_status, setup_headers, setup_body = _request(
            origin + "/api/auth/setup",
            opener=opener,
            data={
                "server_url": jellyfin,
                "username": ADMIN,
                "password": ADMIN_PASSWORD,
                "remember_me": False,
            },
        )
        try:
            setup = json.loads(setup_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            setup = None
        csrf = setup.get("csrf_token") if isinstance(setup, dict) else None
        if setup_status != 201 or not isinstance(csrf, str) or not csrf:
            raise RuntimeError(
                "setup did not create an authenticated CSRF session "
                f"({_response_summary(setup_status, setup_body)})"
            )
        if test_fake_models:
            # Capture the live controller/http child and, while the fake scan is
            # active, assert each observed process is from the copied runtime.
            evidence.roles = _descendants(process.pid)
            _assert_packaged_processes(evidence.roles, runtime)
            _worker_smoke(origin, opener, csrf, root, runtime, process, evidence)
        active_origin, relaunch_origin, opener, csrf = _deployment_lifecycle_smoke(
            origin, opener, csrf, process, data, evidence
        )
        models_status, _h, models_body = _request(active_origin + "/api/models", opener=opener)
        capabilities_status, _h, capabilities_body = _request(
            active_origin + "/api/capabilities", opener=opener
        )
        no_csrf_status, _h, _b = _request(
            active_origin + "/api/auth/logout", opener=opener, data={}
        )
        logout_status, logout_headers, logout_body = _request(
            active_origin + "/api/auth/logout", opener=opener, data={}, csrf=csrf
        )
        evidence.checks["auth_csrf_logout"] = {
            "setup": setup_status,
            "models": models_status,
            "capabilities": capabilities_status,
            "missing_csrf": no_csrf_status,
            "logout": logout_status,
            "session_cookie": bool(setup_headers.get("Set-Cookie")),
            "logout_state": json.loads(logout_body).get("state"),
            "model_payload": "artifacts" in json.loads(models_body),
            "capability_payload": "audio_models" in json.loads(capabilities_body),
            "logout_cookie": bool(logout_headers.get("Set-Cookie")),
        }
        second, second_log = _start(executable, None, root, environment)
        try:
            code = second.wait(timeout=15)
        finally:
            _stop(second)
        evidence.checks["singleton"] = {
            "second_exit": code,
            "second_log_bytes": second_log.stat().st_size,
        }
        _record_roles(evidence, _descendants(process.pid))
        _assert_packaged_processes(evidence.roles, runtime)
        interrupted_scan = None
        if test_fake_models:
            opener, csrf = _login(active_origin)
            status, _headers, body = _request(
                active_origin + "/api/scans", opener=opener, csrf=csrf, data=_FAKE_SCAN
            )
            interrupted_scan = json.loads(body)
            if status != 201 or not isinstance(interrupted_scan.get("queue_id"), int):
                raise RuntimeError("Quit-during-scan fixture did not enqueue")
            _wait_for_scanning(
                active_origin, opener, process, interrupted_scan["queue_id"]
            )
            roles, proof = _live_packaged_worker(root, process.pid, runtime)
            _record_roles(evidence, roles)
            evidence.checks["quit_during_scan"] = {"worker_control": proof}
        # A raw frozen runtime owns the lifecycle state in its top-level process.
        # A macOS application wrapper deliberately keeps the AppKit launcher as
        # that process and runs the embedded frozen runtime beneath it.  Accept
        # only a metadata owner observed in this copied application's live tree.
        controller_pids = {process.pid}
        controller_pids.update(pid for pid, _parent, _command in _descendant_rows(process.pid))
        quit_proof = _request_native_quit(data, controller_pids)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("native launcher Quit did not stop the controller") from error
        fresh, _fresh_log = _start(executable, None, root, environment)
        try:
            _wait_ready(relaunch_origin, fresh)
            if interrupted_scan:
                opener, _csrf = _login(relaunch_origin)
                status, _headers, body = _request(
                    relaunch_origin + "/api/scans/queue", opener=opener
                )
                retained = next((item for item in json.loads(body) if
                                 item.get("queue_id") == interrupted_scan["queue_id"]), None)
                if status != 200 or retained is None or retained.get("status") != "orphaned":
                    raise RuntimeError("full Quit did not preserve an interrupted resumable scan")
                worker_pid = evidence.checks["quit_during_scan"]["worker_control"]["pid"]
                if any(pid == worker_pid for pid, _parent, _command in _process_rows()):
                    raise RuntimeError("interrupted scan worker survived full Quit")
                evidence.checks["quit_during_scan"].update({
                    "queue_retained": True, "status": retained["status"], "worker_stopped": True,
                })
            evidence.checks["full_quit_and_relaunch"] = {
                "relaunch_ready": True,
                "origin": relaunch_origin,
                "quit": quit_proof,
            }
        finally:
            _stop(fresh)
    except BaseException as error:
        failure = error
        raise
    finally:
        _stop(process)
        evidence.checks["runtime_immutable"] = before == _program_snapshot(runtime)
        if failure is not None:
            failure.add_note(_log_tail_note(log))
    if not evidence.checks["runtime_immutable"]:
        raise RuntimeError("read-only copied program directory changed during verification")
    if process.returncode not in _accepted_shutdown_codes():
        detail = _redact(log.read_text(errors="replace")[-_MAX_TEXT:])
        raise RuntimeError(f"runtime exited with status {process.returncode}: {detail}")


def _legacy_smoke(editarr: Path, dependencies: Path | None, root: Path, evidence: Evidence) -> None:
    data = root / "legacy-data"
    data.mkdir()
    port = _available_port()
    process, _log = _start(
        editarr, port, root, _environment(editarr.parent.parent, dependencies, data)
    )
    try:
        status, _headers, body = _wait_ready(f"http://localhost:{port}", process)
        evidence.checks["legacy_wheel_readiness"] = {
            "status": status,
            "state": json.loads(body).get("state"),
        }
    finally:
        _stop(process)


def _recovery_smoke(*args, **kwargs):
    # Imported late: installed_recovery imports this module.
    from scripts.installed_recovery import recovery_smoke

    return recovery_smoke(*args, **kwargs)


def main() -> int:
    args = _arguments()
    root = Path(tempfile.mkdtemp(prefix="Editarr NATIVE-11 verifier "))
    evidence: Evidence | None = None
    try:
        if args.runtime:
            source = args.runtime.resolve()
            if not source.is_dir():
                raise SystemExit(f"runtime directory does not exist: {source}")
            # Preserve the bundle suffix: the executable and inventory layout
            # differ materially between a raw PyInstaller root and a .app.
            copied_name = (
                "Editarr verified ✓ runtime.app"
                if source.suffix == ".app"
                else "Editarr verified ✓ runtime"
            )
            runtime = root / copied_name
            shutil.copytree(source, runtime, symlinks=True)
            metadata = _runtime_inventory(runtime, None)
            executable = _executable(runtime, metadata["target"])
            evidence = Evidence(
                metadata["target"],
                metadata["version"],
                str(executable),
                str(runtime),
                _sha256(executable),
                source_revision=metadata["source_revision"],
                runtime_inventory_sha256=metadata["inventory_sha256"],
            )
            evidence.checks["runtime_metadata"] = {
                "format": 1,
                "path": metadata["inventory_path"],
                "target_matches_request": args.target is None or metadata["target"] == args.target,
            }
            if not evidence.checks["runtime_metadata"]["target_matches_request"]:
                raise RuntimeError(
                    f"requested target {args.target!r} does not match "
                    f"packaged target {metadata['target']!r}"
                )
            _make_read_only(runtime)
            with serve_jellyfin(root / _FAKE_MEDIA_NAME) as jellyfin:
                _native_smoke(
                    executable,
                    runtime,
                    root,
                    evidence,
                    jellyfin=jellyfin,
                    test_fake_models=args.test_fake_models,
                )
                if args.recovery:
                    _recovery_smoke(
                        executable, None, root, evidence, jellyfin=jellyfin, install=runtime
                    )
        else:
            assert args.editarr
            executable = args.editarr.resolve()
            dependencies = args.dependency_site_packages
            dependencies = dependencies.resolve() if dependencies else None
            if not executable.is_file() or (dependencies and not dependencies.is_dir()):
                raise SystemExit("legacy editarr/dependency path does not exist")
            evidence = Evidence(
                "legacy-wheel",
                "legacy-unverified",
                str(executable),
                str(executable.parent.parent),
                _sha256(executable),
            )
            evidence.checks["legacy_dependencies"] = (
                "borrowed-site-packages" if dependencies else "installed-environment"
            )
            _legacy_smoke(executable, dependencies, root, evidence)
            if args.recovery:
                with serve_jellyfin() as jellyfin:
                    _recovery_smoke(executable, dependencies, root, evidence, jellyfin=jellyfin)
        status = 0
    except BaseException as error:
        if evidence is None:
            evidence = Evidence(
                args.target or "unresolved",
                "unresolved",
                "unresolved",
                "unresolved",
                "unresolved",
            )
        evidence.failures.append(_redact(_describe_failure(error))[:_MAX_TEXT])
        status = 1
    evidence.checks["scratch_cleanup"] = _clean_scratch(root, keep=args.keep_root)
    encoded = json.dumps(evidence.output(), sort_keys=True, indent=2)
    if status == 0 and len(encoded) > 64_000:
        evidence.failures.append("verifier evidence exceeded 64 KiB")
        status = 1
        encoded = json.dumps(evidence.output(), sort_keys=True, indent=2)
    print(encoded)
    if args.json_out:
        args.json_out.write_text(encoded + "\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
