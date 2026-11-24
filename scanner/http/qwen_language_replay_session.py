"""Persist the local replay's current audio selection and completed reports."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from shutil import rmtree
from threading import Lock, Thread
from typing import Literal, Self
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from scanner.evaluation.qwen_language_inspection import InspectionTestEvidence
from scanner.evaluation.qwen_language_replay import LanguageReplayReport
from scanner.models import FindingCreate
from scanner.runtime.durability import atomic_write_bytes
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.settings.capabilities import (
    DEFAULT_AUDIO_MODEL,
    DEFAULT_QWEN_MODEL,
    SUPPORTED_AUDIO_MODELS,
    SUPPORTED_QWEN_LANGUAGE_REPLAY_MODELS,
)

from .qwen_language_replay_process import ReplayRunner, RunRequest, Slot

_SLOTS: tuple[Slot, Slot] = ("baseline", "candidate")
_MAX_INSPECTION_TESTS = 24
_FINDING_FIELDS = {"start_seconds", "end_seconds", "label", "category", "subcategory", "severity"}
_OPTION_FIELDS = {"audio_model", "qwen_model", "audio_padding_seconds", "deny_words"}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplayInspectionTest:
    slot: Slot
    rerun_id: str
    evidence: InspectionTestEvidence

    def view(self) -> dict:
        return {
            "rerun_id": self.rerun_id,
            "instructions": self.evidence.instructions,
            **self.evidence.result.model_dump(mode="json"),
        }


class ReplayClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    size_bytes: int = Field(ge=0)
    source: Literal["local"] | None = None


class ReplaySelection(BaseModel):
    """Point to exactly one generation of replay files and its audio source."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    clip: ReplayClip
    local_audio_path: Path | None = None

    @model_validator(mode="after")
    def validate_local_source(self) -> Self:
        path = self.local_audio_path
        if (self.clip.source == "local") != (path is not None):
            raise ValueError("Replay source does not match its selected local path")
        if path is not None and not path.is_absolute():
            raise ValueError("Replay local audio path must be absolute")
        return self

    def work_dir(self, session_dir: Path) -> Path:
        directory = session_dir / self.generation
        if directory.is_symlink():
            raise ValueError("Replay generation must be a private directory")
        return directory


def finding_view(finding: FindingCreate) -> dict:
    """Return only the fields shown in the browser, omitting source transcript details."""
    return finding.model_dump(mode="json", include=_FINDING_FIELDS)


def _report_view(report: LanguageReplayReport) -> dict:
    return {
        "status": report.status,
        "findings": [finding_view(finding) for finding in report.findings],
        "options": report.model_dump(mode="json", include=_OPTION_FIELDS),
    }


@dataclass
class ReplaySession:
    """Keep a selected audio source and its two replay results across server launches."""

    directory: Path
    runner: ReplayRunner
    upload_limit: int
    lock: Lock = field(default_factory=Lock)
    selection: ReplaySelection | None = None
    active: dict | None = None
    error: dict | None = None
    reports: dict[Slot, dict | None] = field(default_factory=lambda: dict.fromkeys(_SLOTS))
    changing_audio: bool = False
    inspection_active: bool = False
    inspection_tests: dict[str, ReplayInspectionTest] = field(default_factory=dict)
    worker: Thread | None = None

    def __post_init__(self) -> None:
        ensure_user_private_directory(self.directory)
        try:
            payload = self.selection_path.read_bytes()
        except FileNotFoundError:
            return
        self.selection = ReplaySelection.model_validate_json(payload)
        self.selection.work_dir(self.directory)
        for slot in _SLOTS:
            try:
                report = LanguageReplayReport.model_validate_json(
                    self.report_path(slot).read_bytes()
                )
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                logger.warning("Could not restore the %s replay report", slot, exc_info=True)
                continue
            if report.status == "complete":
                self.reports[slot] = _report_view(report)

    @property
    def selection_path(self) -> Path:
        return self.directory / "selection.json"

    @property
    def work_dir(self) -> Path:
        if self.selection is None:
            raise ValueError("No replay audio is selected")
        return self.selection.work_dir(self.directory)

    @property
    def uploaded_audio_path(self) -> Path:
        return self.work_dir / "clip.audio"

    @property
    def audio_path(self) -> Path:
        assert self.selection is not None
        return self.selection.local_audio_path or self.uploaded_audio_path

    def report_path(self, slot: Slot) -> Path:
        return self.work_dir / f"{slot}.json"

    def require_idle(self) -> None:
        if self.active or self.changing_audio:
            raise HTTPException(409, "Wait for the current audio selection or replay to finish.")
        if self.inspection_active:
            raise HTTPException(409, "Wait for Qwen to finish inspecting this decision.")

    def record_inspection_test(
        self, context_id: str, slot: Slot, evidence: InspectionTestEvidence
    ) -> ReplayInspectionTest:
        """Keep the latest completed test for this context while the session lock is held."""
        test = ReplayInspectionTest(slot, uuid4().hex, evidence)
        self.inspection_tests.pop(context_id, None)
        self.inspection_tests[context_id] = test
        if len(self.inspection_tests) > _MAX_INSPECTION_TESTS:
            self.inspection_tests.pop(next(iter(self.inspection_tests)))
        return test

    def select_audio(
        self, clip: ReplayClip, *, local_path: Path | None = None, upload: Path | None = None
    ) -> dict:
        """Publish a fresh selection and its files before dropping the prior reports."""
        previous_dir = self.work_dir if self.selection is not None else None
        selection = ReplaySelection(
            version=1, generation=uuid4().hex, clip=clip, local_audio_path=local_path
        )
        work_dir = selection.work_dir(self.directory)
        work_dir.mkdir(mode=0o700)
        try:
            if upload is not None:
                upload.replace(work_dir / "clip.audio")
            atomic_write_bytes(
                self.selection_path,
                selection.model_dump_json().encode(),
                prefix=".selection-",
            )
        except OSError:
            rmtree(work_dir, ignore_errors=True)
            raise
        self.selection = selection
        self.reports = dict.fromkeys(_SLOTS)
        self.error = None
        self.inspection_tests.clear()
        if previous_dir is not None:
            rmtree(previous_dir, ignore_errors=True)
        return self.snapshot()

    def snapshot(self) -> dict:
        """Return an API status snapshot while the session lock is held."""
        clip = (
            self.selection.clip.model_dump(mode="json", exclude_none=True)
            if self.selection
            else None
        )
        return {
            "audio": clip,
            "active": self.active,
            "error": self.error,
            "reports": self.reports.copy(),
            "options": {
                "audio_models": SUPPORTED_AUDIO_MODELS,
                "qwen_models": SUPPORTED_QWEN_LANGUAGE_REPLAY_MODELS,
                "audio_model": DEFAULT_AUDIO_MODEL,
                "qwen_model": DEFAULT_QWEN_MODEL,
                "upload_limit_bytes": self.upload_limit,
            },
        }

    def run(self, options: RunRequest) -> None:
        """Run inference off the server event loop and publish a complete result."""
        slot = options.slot
        pending = self.work_dir / f".{slot}-pending.json"

        def update(message: str) -> None:
            with self.lock:
                self.active = {"slot": slot, "message": message}

        try:
            self.runner(self.audio_path, pending, options, update)
            report = LanguageReplayReport.model_validate_json(pending.read_bytes())
            if report.status != "complete":
                raise RuntimeError(f"Qwen did not complete ({report.status}).")
            summary = _report_view(report)
            with self.lock:
                pending.replace(self.report_path(slot))
                self.reports[slot] = summary
                self.inspection_tests = {
                    context_id: test
                    for context_id, test in self.inspection_tests.items()
                    if test.slot != slot
                }
        except Exception as exc:
            logger.exception("Could not complete %s replay", slot)
            message = str(exc).strip()[:500] or "Qwen did not complete."
            with self.lock:
                self.error = {"slot": slot, "message": message}
        finally:
            try:
                pending.unlink(missing_ok=True)
            finally:
                with self.lock:
                    self.active = None
