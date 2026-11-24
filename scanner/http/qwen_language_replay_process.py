"""Run a fresh local language replay process for each browser request."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scanner.settings.capabilities import (
    DEFAULT_AUDIO_MODEL,
    DEFAULT_QWEN_MODEL,
    SUPPORTED_AUDIO_MODELS,
    SUPPORTED_QWEN_LANGUAGE_REPLAY_MODELS,
)

if TYPE_CHECKING:
    from scanner.evaluation.qwen_language_inspection import (
        InspectionAnswer,
        InspectionContext,
        InspectionRerun,
        InspectionTestEvidence,
    )

Slot = Literal["baseline", "candidate"]
InspectionAction = Literal["ask", "rerun"]
_INSPECTION_TIMEOUT_SECONDS = 180
logger = logging.getLogger(__name__)


class RunRequest(BaseModel):
    """Limit browser input to replay options supported by the scanner CLI."""

    model_config = ConfigDict(extra="forbid")

    slot: Slot
    audio_model: str = DEFAULT_AUDIO_MODEL
    qwen_model: str = DEFAULT_QWEN_MODEL
    audio_padding_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    deny_words: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list, max_length=32
    )

    @field_validator("audio_model")
    @classmethod
    def valid_audio_model(cls, model: str) -> str:
        if model not in SUPPORTED_AUDIO_MODELS:
            raise ValueError("Unsupported transcription model")
        return model

    @field_validator("qwen_model")
    @classmethod
    def valid_qwen_model(cls, model: str) -> str:
        if model not in SUPPORTED_QWEN_LANGUAGE_REPLAY_MODELS:
            raise ValueError("Unsupported Qwen model")
        return model


ReplayRunner = Callable[[Path, Path, RunRequest, Callable[[str], None]], None]


class ReplayProcess:
    def __init__(self, repo_dir: Path, cache_dir: Path | None):
        self.repo_dir = repo_dir
        self.cache_dir = cache_dir
        self._lock = Lock()
        self._closed = False
        self._process: subprocess.Popen[str] | None = None

    def _start(self, argv: list[str], *, inspection: bool = False) -> subprocess.Popen[str]:
        with self._lock:
            if self._closed:
                raise RuntimeError("The replay server is shutting down")
            if self._process is not None:
                raise RuntimeError("Another replay process is running")
            process = self._process = subprocess.Popen(
                argv,
                cwd=self.repo_dir,
                stdin=subprocess.PIPE if inspection else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if inspection else subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return process

    def _python_arguments(self, work_dir: Path) -> list[str]:
        # Ignore existing bytecode so even edits saved within the same second take effect.
        return [sys.executable, "-u", "-B", "-X", f"pycache_prefix={work_dir / '.empty-pycache'}"]

    def __call__(
        self,
        audio: Path,
        output: Path,
        options: RunRequest,
        progress: Callable[[str], None],
    ) -> None:
        argv = [
            *self._python_arguments(output.parent),
            "-m",
            "scanner.commands.qwen_language_replay_cli",
            "run",
            str(audio),
            "--output",
            str(output),
            "--audio-model",
            options.audio_model,
            "--qwen-model",
            options.qwen_model,
        ]
        if self.cache_dir is not None:
            argv.extend(["--cache-dir", str(self.cache_dir)])
        if options.audio_padding_seconds is not None:
            argv.extend(["--audio-padding-seconds", str(options.audio_padding_seconds)])
        argv.extend(f"--deny-word={word}" for word in options.deny_words)

        process = self._start(argv)

        error = "Qwen did not complete. Check the server terminal for details."
        try:
            with process:
                assert process.stdout is not None
                for line in process.stdout:
                    message = line.strip()
                    percentage, _, _ = message.partition("%")
                    if percentage.isdigit() and "%" in message:
                        progress(message)
                    elif message.startswith("Error:"):
                        error = message
                    elif message.startswith("Qwen did not complete"):
                        error = message.partition(" Report saved")[0]
                    elif message.startswith(
                        ("Transcription returned no timed speech", "Qwen needs")
                    ):
                        error = message
                    if message:
                        logger.info("%s", message)
                if process.wait():
                    raise RuntimeError(error)
        finally:
            with self._lock:
                self._process = None

    def inspect(
        self,
        action: InspectionAction,
        evidence: InspectionContext,
        text: str,
        work_dir: Path,
        *,
        latest_test: InspectionTestEvidence | None = None,
    ) -> InspectionAnswer | InspectionRerun:
        """Ask or rerun in an MLX subprocess, sending evidence through a private pipe."""
        from scanner.evaluation.qwen_language_inspection import InspectionAnswer, InspectionRerun

        command = {"action": action, "evidence": evidence.model_dump(mode="json"), "text": text}
        if latest_test is not None:
            command["latest_test"] = latest_test.model_dump(mode="json")
        payload = json.dumps(command, ensure_ascii=False)
        argv = [*self._python_arguments(work_dir), "-m", "scanner.commands.qwen_language_inspect"]
        process = self._start(argv, inspection=True)
        try:
            with process:
                try:
                    output, errors = process.communicate(
                        payload, timeout=_INSPECTION_TIMEOUT_SECONDS
                    )
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.communicate()
                    raise RuntimeError(
                        "Qwen inspection timed out. Try again with this batch."
                    ) from exc
                if errors.strip():
                    logger.info("%s", errors.strip())
                if process.returncode:
                    try:
                        error = json.loads(output)["error"]
                    except (ValueError, TypeError, KeyError):
                        error = None
                    if not isinstance(error, str) or not error.strip():
                        error = "Qwen inspection failed. Check the replay server log for details."
                    raise RuntimeError(error)
                result = InspectionAnswer if action == "ask" else InspectionRerun
                try:
                    return result.model_validate_json(output)
                except ValueError as exc:
                    raise RuntimeError("Qwen inspection returned an unreadable result.") from exc
        finally:
            with self._lock:
                self._process = None

    def close(self) -> None:
        """Terminate and reap active inference when the local web server exits."""
        with self._lock:
            self._closed = True
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            except ProcessLookupError:
                pass
