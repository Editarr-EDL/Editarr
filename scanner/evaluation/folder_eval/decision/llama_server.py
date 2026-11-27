"""llama.cpp weights location, `llama-server` resolution, and one private server process."""

from __future__ import annotations

import atexit
import functools
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from ..backends import BackendUnavailable

# One INFO line per request would flood `run` logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

STARTUP_TIMEOUT_S = 300.0
REQUEST_TIMEOUT_S = 600.0
MODELS_DIR_ENV = "EDITARR_DECISION_MODELS_DIR"


def models_dir() -> Path:
    """Root of the GGUF weights and unpacked llama.cpp releases used by the llama.cpp ids.

    `EDITARR_DECISION_MODELS_DIR` wins; otherwise `evaluation/llama-cpp` under Editarr's
    model cache (`EDITARR_MODEL_CACHE`, or the platform cache directory).
    """
    configured = os.environ.get(MODELS_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    from scanner.settings.config import settings

    return settings.model_cache / "evaluation" / "llama-cpp"


def server_binary(models_dir: Path, *, bundled: bool = False) -> str | None:
    """Resolve llama-server: an executable `EDITARR_LLAMA_SERVER`; else, when `bundled`,
    the newest release unpacked under `<models_dir>/llama.cpp-b<build>/`; else
    `llama-server` on PATH. Only specs that opt in use the bundled build, so other ids
    keep the runtime their recorded results came from."""

    configured = os.environ.get("EDITARR_LLAMA_SERVER")
    if configured:
        path = Path(configured)
        return configured if path.is_file() and os.access(path, os.X_OK) else None
    if bundled:
        releases = []
        for candidate in models_dir.glob("llama.cpp-b*/*/llama-server"):
            match = re.search(r"llama\.cpp-b(\d+)", str(candidate))
            if match and os.access(candidate, os.X_OK):
                releases.append((int(match.group(1)), str(candidate)))
        if releases:
            return max(releases)[1]
    return shutil.which("llama-server")


def runtime_label(version: str) -> str:
    """Short runtime tag such as `llama.cpp b12556` from a `--version` string."""

    match = re.search(r"build (\d+)", version)
    return f"llama.cpp b{match.group(1)}" if match else f"llama.cpp {version}"


def server_version(binary: str | None) -> str:
    if binary is None:
        return "llama-server not installed"
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown version"
    for line in (out.stdout + out.stderr).splitlines():
        if line.startswith("version:"):
            return line.removeprefix("version:").strip()
    return "unknown version"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


class LlamaServerProcess:
    """Lazily started, thread-safe llama-server on a free localhost port.

    `command(port)` returns the full command line; output goes to `log_path`. The first
    failed startup is remembered so later calls fail fast instead of waiting again.
    """

    def __init__(self, command: Callable[[int], list[str]], log_path: Path):
        self._command = command
        self._log_path = log_path
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._exit_hook: Callable[[], None] | None = None
        self._client: httpx.Client | None = None
        self._base_url = ""
        self._startup_error: BackendUnavailable | None = None
        self.peak_rss_bytes = 0

    def ensure_started(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            if self._startup_error is not None:
                raise self._startup_error
            try:
                self._start()
            except BackendUnavailable as exc:
                self._startup_error = exc
                raise

    def post(self, path: str, body: dict) -> dict:
        client = self._client
        if client is None:
            raise RuntimeError("llama-server is not running")
        response = client.post(f"{self._base_url}{path}", json=body)
        if response.status_code != 200:
            raise RuntimeError(f"{path} HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    def get(self, path: str) -> dict:
        client = self._client
        if client is None:
            raise RuntimeError("llama-server is not running")
        response = client.get(f"{self._base_url}{path}")
        if response.status_code != 200:
            raise RuntimeError(f"{path} HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            process, self._process = self._process, None
            if process is not None:
                _terminate(process)
            if self._exit_hook is not None:
                atexit.unregister(self._exit_hook)
                self._exit_hook = None

    def rss_bytes(self) -> int:
        """Current resident memory of the server process, or 0 if unknown."""

        process = self._process
        if process is None:
            return 0
        try:
            out = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(process.pid)],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            return int(out) * 1024 if out else 0
        except (OSError, ValueError):
            return 0

    def sample_rss(self) -> None:
        self.peak_rss_bytes = max(self.peak_rss_bytes, self.rss_bytes())

    def _start(self) -> None:
        port = _free_port()
        command = self._command(port)
        try:
            with self._log_path.open("wb") as log:
                self._process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise BackendUnavailable(
                f"unavailable: cannot start llama-server ({command[0]}): {exc}"
            ) from exc
        # Stop the server if the caller exits without close(), e.g. after an exception.
        self._exit_hook = functools.partial(_terminate, self._process)
        atexit.register(self._exit_hook)
        self._base_url = f"http://127.0.0.1:{port}"
        self._client = httpx.Client(
            timeout=REQUEST_TIMEOUT_S, limits=httpx.Limits(max_connections=64)
        )
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                tail = self._log_path.read_text(errors="replace")[-1500:]
                self._stop_unlocked()
                raise BackendUnavailable(
                    f"unavailable: llama-server exited during startup:\n{tail}"
                )
            try:
                if self._client.get(f"{self._base_url}/health", timeout=2).status_code == 200:
                    self.sample_rss()
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        self._stop_unlocked()
        raise BackendUnavailable(
            f"unavailable: llama-server not healthy after {STARTUP_TIMEOUT_S}s"
        )

    def _stop_unlocked(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._process is not None:
            _terminate(self._process)
            self._process = None
        if self._exit_hook is not None:
            atexit.unregister(self._exit_hook)
            self._exit_hook = None
