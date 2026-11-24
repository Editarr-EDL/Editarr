"""Replay Editarr's Qwen audio scan with a reusable transcript."""

from __future__ import annotations

import json
from collections.abc import Callable
from difflib import unified_diff
from hashlib import file_digest
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from scanner.model_acquisition import _cross_process_lock, artifact_ids_for_backend, manifest
from scanner.models import FindingCreate
from scanner.pipeline.apple_speech import speech_locale, transcript_revision
from scanner.pipeline.audio import resolve_audio_model_name, scan_audio
from scanner.pipeline.audio_words import effective_audio_filter_words
from scanner.pipeline.qwen_language_trace import LanguageDecisionAttempt
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.settings.capabilities import APPLE_SPEECH_MODEL, SUPPORTED_QWEN_LANGUAGE_REPLAY_MODELS
from scanner.settings.model_scan_settings import ModelScanSettings


class LanguageReplayReport(BaseModel):
    """Identify the immutable audio and transcript used for a set of findings."""

    version: Literal[1] = 1
    audio_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    transcript_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    audio_model: str
    transcription_model: str
    qwen_model: str
    qwen_revision: str | None
    audio_padding_seconds: float = Field(ge=0, allow_inf_nan=False)
    deny_words: list[str]
    status: Literal["complete", "no_inputs", "unsupported", "partial", "failed"]
    findings: list[FindingCreate]
    decision_trace: list[LanguageDecisionAttempt] | None = None


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest()


def replay_qwen_language(
    audio: Path,
    *,
    cache_dir: Path,
    audio_model: str,
    qwen_model: str,
    audio_padding_seconds: float,
    deny_words: list[str] | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
) -> LanguageReplayReport:
    """Run the current language prompts while reusing transcription work across runs."""
    if not audio.is_file():
        raise ValueError(f"Audio file does not exist or is not a regular file: {audio}")
    if qwen_model not in SUPPORTED_QWEN_LANGUAGE_REPLAY_MODELS:
        raise ValueError(f"Unsupported Qwen language replay model: {qwen_model}")
    ModelScanSettings(audio_padding_seconds=audio_padding_seconds)
    audio_hash = _sha256(audio)
    audio_model = resolve_audio_model_name(audio_model)
    model_manifest = manifest()
    if audio_model == APPLE_SPEECH_MODEL:
        transcriber_id = "apple-speech"
        revision = transcript_revision(speech_locale())
    else:
        artifact = model_manifest.artifact(artifact_ids_for_backend(audio_model)[0])
        transcriber_id, revision = artifact.id, artifact.revision
    qwen_artifact = model_manifest.artifact(qwen_model)
    directory = ensure_user_private_directory(cache_dir / "v1" / audio_hash)
    transcript_path = directory / f"{transcriber_id}-{revision}.json"
    _, runtime_deny = effective_audio_filter_words(None, deny_words)
    provenance: list[str] = []
    decision_trace: list[LanguageDecisionAttempt] = []

    with _cross_process_lock(transcript_path.with_suffix(".lock"), lambda: False):
        findings = scan_audio(
            str(audio),
            video_id=audio_hash,
            model_name=audio_model,
            filter_name="qwen",
            deny_words=deny_words,
            transcript_cache_path=str(transcript_path),
            progress_callback=progress_callback,
            qwen_model=qwen_model,
            provenance_callback=provenance.append,
            audio_padding_seconds=audio_padding_seconds,
            decision_callback=decision_trace.append,
        )
        transcript_hash = _sha256(transcript_path)

    return LanguageReplayReport.model_validate(
        {
            "audio_sha256": audio_hash,
            "transcript_sha256": transcript_hash,
            "audio_model": audio_model,
            "transcription_model": f"{transcriber_id}@{revision}",
            "qwen_model": qwen_model,
            "qwen_revision": qwen_artifact.revision,
            "audio_padding_seconds": audio_padding_seconds,
            "deny_words": sorted(runtime_deny),
            "status": provenance[-1] if provenance else "failed",
            "findings": findings,
            "decision_trace": decision_trace,
        }
    )


def _finding_lines(findings: list[FindingCreate]) -> list[str]:
    lines = []
    for finding in findings:
        end = f"{finding.end_seconds:.3f}s" if finding.end_seconds is not None else "?"
        severity = finding.severity.value if finding.severity else "unrated"
        label = json.dumps(finding.label or "", ensure_ascii=False)
        line = (
            f"{finding.start_seconds:.3f}s–{end}  {label}  "
            f"{finding.category.value}/{finding.subcategory.value}  {severity}"
        )
        lines.append((finding.start_seconds, line))
    return [line for _, line in sorted(lines)]


def read_comparable_replays(
    baseline_path: Path, candidate_path: Path
) -> tuple[LanguageReplayReport, LanguageReplayReport]:
    """Load two completed reports and ensure their findings share the same audio and transcript."""
    baseline = LanguageReplayReport.model_validate_json(baseline_path.read_bytes())
    candidate = LanguageReplayReport.model_validate_json(candidate_path.read_bytes())
    for path, report in ((baseline_path, baseline), (candidate_path, candidate)):
        if report.status != "complete":
            raise ValueError(f"Cannot compare incomplete Qwen run {path} ({report.status})")
    if baseline.audio_sha256 != candidate.audio_sha256:
        raise ValueError("Runs used different audio files")
    if baseline.transcript_sha256 != candidate.transcript_sha256:
        raise ValueError("Runs used different transcripts")
    return baseline, candidate


def language_replay_setting_changes(
    baseline: LanguageReplayReport, candidate: LanguageReplayReport
) -> list[str]:
    """Describe any model or input setting difference recorded by the two reports."""
    notes = []
    for option in (
        "transcription_model",
        "qwen_model",
        "qwen_revision",
        "audio_padding_seconds",
        "deny_words",
    ):
        before, after = getattr(baseline, option), getattr(candidate, option)
        if before != after:
            notes.append(f"Setting changed ({option}): {before!r} → {after!r}")
    return notes


def format_language_replay_diff(
    baseline: LanguageReplayReport,
    candidate: LanguageReplayReport,
    *,
    baseline_label: str,
    candidate_label: str,
) -> str:
    """Describe timed finding changes as a unified diff for CLI and existing API clients."""
    lines = [f"Findings: {len(baseline.findings)} baseline, {len(candidate.findings)} candidate"]
    lines.extend(language_replay_setting_changes(baseline, candidate))
    changes = list(
        unified_diff(
            _finding_lines(baseline.findings),
            _finding_lines(candidate.findings),
            fromfile=baseline_label,
            tofile=candidate_label,
            lineterm="",
            n=0,
        )
    )
    return "\n".join([*lines, *(changes or ["No finding changes."])])


def compare_language_replays(baseline_path: Path, candidate_path: Path) -> str:
    """Describe timed finding changes for two completed runs of the same transcript."""
    baseline, candidate = read_comparable_replays(baseline_path, candidate_path)
    return format_language_replay_diff(
        baseline, candidate, baseline_label=str(baseline_path), candidate_label=str(candidate_path)
    )
