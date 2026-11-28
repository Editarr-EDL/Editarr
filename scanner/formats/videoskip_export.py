"""Serialize effective review cues as a standalone VideoSkip file."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from scanner.models import EditAction, McfCategory, McfCategoryFamily, Severity

from .videoskip import MAX_CONTENT_BYTES, MAX_CUES, MAX_TEXT, _is_label

_LABEL_BY_SUBCATEGORY = {
    "sex": "sex",
    "nudity": "nudity",
    "violence": "violence",
    "gore": "gore",
    "swearing": "profanity",
    "discrimination": "hate",
    "drugs": "drugs",
    "alcohol": "alcohol",
    "cigarettes": "smoking",
    "fear": "frightening",
    "other": "other",
    "tedious": "boring",
}
_LABEL_BY_FAMILY = {
    McfCategoryFamily.SEX: "sex",
    McfCategoryFamily.NUDITY: "nudity",
    McfCategoryFamily.VIOLENCE: "violence",
    McfCategoryFamily.LANGUAGE: "profanity",
    McfCategoryFamily.DISCRIMINATION: "hate",
    McfCategoryFamily.DRUGS: "drugs",
    McfCategoryFamily.FEAR: "frightening",
}
_GRADE = {Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}
_MAX_SECONDS = 10_000 * 3_600
_TEXT_REPLACEMENTS = str.maketrans(
    {
        "(": "（",
        ")": "）",
        "[": "［",
        "]": "］",
        "<": "＜",
        ">": "＞",
        "&": "＆",
    }
)


class VideoSkipExportFilter(Protocol):
    """Fields consumed from an effective saved-review filter."""

    id: str
    start_seconds: float
    end_seconds: float | None
    category: McfCategoryFamily | None
    subcategory: McfCategory | None
    severity: Severity | None
    edit_action: EditAction
    selected: bool
    mcf_comment: str | None
    details: str | None
    label: str | None


@dataclass(frozen=True)
class VideoSkipExportIssue:
    code: str
    filter_id: str
    message: str
    omitted: bool = False


@dataclass(frozen=True)
class VideoSkipExportResult:
    content: bytes
    exported_count: int
    issues: tuple[VideoSkipExportIssue, ...]


def export_videoskip(
    filters: Sequence[VideoSkipExportFilter],
    *,
    reference_seconds: float | None = None,
    reference_description: str | None = None,
) -> VideoSkipExportResult:
    """Render selected skip/mute cues and report lossy mappings or omitted cues."""
    header = _reference(reference_seconds, reference_description).encode("utf-8")
    size = len(header)
    cues: list[tuple[int, int, bytes]] = []
    issues: list[VideoSkipExportIssue] = []
    for cue in filters:
        if not cue.selected:
            continue
        if cue.edit_action not in (EditAction.SKIP, EditAction.MUTE):
            issues.append(
                VideoSkipExportIssue(
                    "unsupported_action",
                    cue.id,
                    "Only skip and mute can be exported.",
                    omitted=True,
                )
            )
            continue
        start = videoskip_milliseconds(cue.start_seconds)
        end = videoskip_milliseconds(cue.end_seconds)
        if start is None or end is None or end <= start:
            issues.append(
                VideoSkipExportIssue(
                    "invalid_cue_range",
                    cue.id,
                    "This cue needs an increasing range at millisecond precision.",
                    omitted=True,
                )
            )
            continue
        label = _cue_label(cue, issues)
        content = f"{_timestamp(start)} --> {_timestamp(end)}\n{label}\n\n".encode("utf-8")
        cues.append((start, end, content))
        if len(cues) > MAX_CUES:
            raise ValueError("VideoSkip export exceeds the cue limit")
        size += len(content)
        if size > MAX_CONTENT_BYTES:
            raise ValueError("VideoSkip export exceeds the file size limit")
    ordered = sorted(cues, key=lambda cue: cue[:2])
    return VideoSkipExportResult(
        header + b"".join(content for _, _, content in ordered), len(cues), tuple(issues)
    )


def _cue_label(cue: VideoSkipExportFilter, issues: list[VideoSkipExportIssue]) -> str:
    category = _LABEL_BY_SUBCATEGORY.get(cue.subcategory) if cue.subcategory else None
    if category is None:
        category = _LABEL_BY_FAMILY.get(cue.category, "other") if cue.category else "other"
        issues.append(
            VideoSkipExportIssue(
                "category_mapped",
                cue.id,
                f"VideoSkip uses the broader {category} label for this cue.",
            )
        )
    if cue.severity is None:
        issues.append(
            VideoSkipExportIssue(
                "severity_defaulted",
                cue.id,
                "VideoSkip uses Medium (2) for this unrated cue.",
            )
        )
    action = " audio" if cue.edit_action == EditAction.MUTE else ""
    label = f"{category}{action} {_GRADE[cue.severity or Severity.MEDIUM]}"
    description = _safe_text(cue.mcf_comment or cue.details or cue.label or "")
    if description:
        label += f" ({description[: MAX_TEXT - len(label) - 3].rstrip()})"
    return label


def _reference(seconds: float | None, description: str | None) -> str:
    label = _safe_text(description or "")
    if seconds is None:
        if label:
            raise ValueError("A VideoSkip reference description requires a timestamp")
        return ""
    milliseconds = videoskip_milliseconds(seconds)
    if milliseconds is None:
        raise ValueError("VideoSkip reference timestamp is out of range")
    if not label:
        return f"{_timestamp(milliseconds)}\n\n"
    if not _is_label(label):
        label = f"Reference: {label}"
    return f"{_timestamp(milliseconds)}\n{label[:MAX_TEXT]}\n\n"


def _safe_text(value: str) -> str:
    """Keep captions on one line and protect cue, action, and region delimiters."""
    text = value.replace("-->", "→").replace("//", "／／").translate(_TEXT_REPLACEMENTS)
    return " ".join("".join(char if char.isprintable() else " " for char in text).split())


def videoskip_milliseconds(seconds: float | None) -> int | None:
    """Round a media timestamp to the milliseconds that a VideoSkip file stores."""
    if seconds is None or not isfinite(seconds) or not 0 <= seconds < _MAX_SECONDS:
        return None
    value = round(seconds * 1000)
    return value if value < _MAX_SECONDS * 1000 else None


def _timestamp(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
