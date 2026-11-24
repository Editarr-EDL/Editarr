"""Inspect an unchanged replay report and run single-batch experiments."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, HTTPException
from pydantic import Field, field_validator

from scanner.evaluation.qwen_language_inspection import (
    InspectionAnswer,
    InspectionContext,
    InspectionRerun,
    InspectionTarget,
    InspectionTestEvidence,
    inspect_context,
)
from scanner.evaluation.qwen_language_replay import LanguageReplayReport
from scanner.settings.config import settings

from .qwen_language_replay_process import InspectionAction, Slot
from .qwen_language_replay_session import ReplaySession


class InspectionRunner(Protocol):
    def __call__(
        self,
        action: InspectionAction,
        evidence: InspectionContext,
        text: str,
        work_dir: Path,
        *,
        latest_test: InspectionTestEvidence | None = None,
    ) -> InspectionAnswer | InspectionRerun: ...


_CONTEXT_FIELDS = {
    "evidence",
    "kind",
    "qwen_model",
    "qwen_revision",
    "target",
    "flagged",
    "finding",
    "instructions",
    "inputs",
    "response",
    "accepted",
    "attempts",
    "batch_start_seconds",
    "batch_end_seconds",
    "windows",
}


class InspectionRequest(InspectionTarget):
    """Locate an occurrence in the selected saved replay, using the report's timings."""

    slot: Slot


class InspectionActionRequest(InspectionRequest):
    context_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class AskInspectionRequest(InspectionActionRequest):
    question: str = Field(min_length=1, max_length=2048)
    rerun_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, question: str) -> str:
        if not question.strip():
            raise ValueError("Enter a question for Qwen.")
        return question.strip()


class RerunInspectionRequest(InspectionActionRequest):
    instructions: str = Field(min_length=1, max_length=32768)

    @field_validator("instructions")
    @classmethod
    def nonblank_instructions(cls, instructions: str) -> str:
        if not instructions.strip():
            raise ValueError("Enter instructions for the batch rerun.")
        return instructions


def register_inspection_routes(
    app: FastAPI, session: ReplaySession, cache_dir: Path | None, inspector: InspectionRunner
) -> None:
    """Bind evidence, model questions, and reruns to the current local session."""
    transcript_cache = cache_dir or settings.cache_dir / "qwen-language-replays"

    def resolve(request: InspectionRequest) -> tuple[InspectionContext, str]:
        with session.lock:
            if session.reports[request.slot] is None:
                raise HTTPException(404, f"Run the {request.slot} before inspecting its decisions.")
            try:
                report_path = session.report_path(request.slot)
                report_bytes = report_path.read_bytes()
            except OSError as exc:
                raise HTTPException(409, "The saved replay report is unavailable.") from exc
        try:
            report = LanguageReplayReport.model_validate_json(report_bytes)
        except ValueError as exc:
            raise HTTPException(409, "The saved replay report could not be read.") from exc
        try:
            target = InspectionTarget(
                start_seconds=request.start_seconds,
                end_seconds=request.end_seconds,
                label=request.label,
            )
            evidence = inspect_context(report, target, cache_dir=transcript_cache)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(404, "The cached transcript is unavailable.") from exc
        digest = sha256(report_bytes)
        digest.update(report_path.parent.name.encode())
        digest.update(request.slot.encode())
        digest.update(evidence.model_dump_json().encode())
        return evidence, digest.hexdigest()

    def run_model(request: InspectionActionRequest, action: InspectionAction, text: str) -> dict:
        with session.lock:
            session.require_idle()
            session.inspection_active = True
        try:
            evidence, context_id = resolve(request)
            if context_id != request.context_id:
                raise HTTPException(409, "The saved decision changed. Open it again and retry.")
            latest_test = None
            if isinstance(request, AskInspectionRequest):
                with session.lock:
                    latest = session.inspection_tests.get(context_id)
                latest_id = latest.rerun_id if latest is not None else None
                if "rerun_id" in request.model_fields_set and request.rerun_id != latest_id:
                    raise HTTPException(
                        409,
                        "The batch test changed or expired. Open the decision again to continue.",
                    )
                latest_test = latest.evidence if latest is not None else None
            try:
                result = inspector(
                    action, evidence, text, session.work_dir, latest_test=latest_test
                )
            except RuntimeError as exc:
                raise HTTPException(502, str(exc)[:500]) from exc
            except OSError as exc:
                raise HTTPException(
                    502, "The local Qwen inspection process could not start."
                ) from exc
            if isinstance(result, InspectionRerun):
                with session.lock:
                    test = session.record_inspection_test(
                        context_id,
                        request.slot,
                        InspectionTestEvidence(instructions=text, result=result),
                    )
                return test.view()
            return result.model_dump(mode="json")
        finally:
            with session.lock:
                session.inspection_active = False

    @app.post("/api/inspect/context")
    def context(request: InspectionRequest) -> dict:
        evidence, context_id = resolve(request)
        with session.lock:
            latest = session.inspection_tests.get(context_id)
        return {
            **evidence.model_dump(mode="json", include=_CONTEXT_FIELDS),
            "context_id": context_id,
            "latest_rerun": latest.view() if latest is not None else None,
        }

    @app.post("/api/inspect/ask")
    def ask(request: AskInspectionRequest) -> dict:
        return run_model(request, "ask", request.question)

    @app.post("/api/inspect/rerun")
    def rerun(request: RerunInspectionRequest) -> dict:
        return run_model(request, "rerun", request.instructions)
