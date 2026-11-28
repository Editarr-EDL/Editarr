"""Read VideoSkip ranges and synchronization metadata without applying offsets."""

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

from scanner.modeling.model_videoskip import VideoSkipImportIssue, VideoSkipSync
from scanner.models import MCF_CATEGORY_TO_FAMILY, EditAction, McfChannel, Severity

from .edl import ParsedEdl, ParsedEdlFilter

MAX_CONTENT_BYTES = 1_048_576
MAX_CUES = 10_000
MAX_LINES = 40_000
MAX_TEXT = 4_096

_CLOCK = r"-?\d{1,4}:\d{2}(?::\d{2})?(?:[.,]\d{1,9})?"
_TIME_RANGE = re.compile(
    rf"^({_CLOCK})(\[[^\]]{{1,100}}\])?\s*-->\s*({_CLOCK})"
    r"(?:\s*([a-zA-Z].*))?$"
)
_POINT = re.compile(rf"^({_CLOCK})(?:\s+(.*))?$")
_SCREENSHOT = re.compile(r"^data:(image[/ ][a-z0-9.+-]+);base64,(.*)$", re.IGNORECASE)
_SUBCATEGORIES = {
    "sex": "sex", "nud": "nudity", "vio": "violence", "gor": "gore",
    "pro": "swearing", "cur": "swearing", "hat": "discrimination",
    "alc": "alcohol", "dru": "drugs", "smo": "cigarettes",
    "fri": "fear", "sca": "fear", "int": "fear",
    "oth": "other", "bor": "tedious", "sub": "drugs",
}
_SEVERITIES = {"1": Severity.LOW, "2": Severity.MEDIUM, "3": Severity.HIGH}

VideoSkipAction = Literal["skip", "mute", "blank", "blur", "fast", "disabled"]


class VideoSkipParseError(ValueError):
    """A bounded, line-aware VideoSkip file error."""

    def __init__(self, code: str, message: str, line_number: int | None = None):
        super().__init__(message)
        self.code = code
        self.line_number = line_number


@dataclass(frozen=True)
class VideoSkipCue:
    line_number: int
    start_seconds: float
    end_seconds: float
    source_label: str
    source_action: VideoSkipAction
    subcategory: str | None
    severity: Severity | None
    recognized_category: bool
    comment: str | None
    region: str | None

    def to_filter(self) -> ParsedEdlFilter | None:
        """Map actions Editarr can perform to the common local import input."""
        if not self.recognized_category or self.source_action not in {"skip", "mute"}:
            return None
        audio = self.source_action == "mute"
        return ParsedEdlFilter(
            start_seconds=self.start_seconds,
            end_seconds=self.end_seconds,
            category=MCF_CATEGORY_TO_FAMILY[self.subcategory] if self.subcategory else None,
            subcategory=self.subcategory,
            severity=self.severity,
            mcf_channel=McfChannel.AUDIO if audio else McfChannel.BOTH,
            mcf_comment=self.comment,
            source="audio" if audio else "visual",
            label=self.comment or None,
            details=self.source_label,
            group_key=None,
            confidence=None,
            edit_action=EditAction.MUTE if audio else EditAction.SKIP,
            line_number=self.line_number,
        )


@dataclass(frozen=True)
class ParsedVideoSkip:
    sha256: str
    sync: VideoSkipSync
    cues: list[VideoSkipCue]
    issues: list[VideoSkipImportIssue]

    def to_import(self) -> ParsedEdl:
        """Return the playable subset, leaving unsupported cues in the source report."""
        return ParsedEdl([row for cue in self.cues if (row := cue.to_filter()) is not None])


def _seconds(value: str) -> float:
    absolute = value.removeprefix("-").replace(",", ".")
    fields = absolute.split(":")
    if any(float(part) >= 60 for part in fields[1:]):
        raise ValueError("Minutes or seconds are out of range")
    total = 0.0
    for part in fields:
        total = total * 60 + float(part)
    return -total if value.startswith("-") else total


def _action(prefixes: set[str], raw_label: str) -> VideoSkipAction:
    if "//" in raw_label:
        return "disabled"
    for tags, action in (
        ({"aud", "sou", "spe", "wor", "mut"}, "mute"),
        ({"vid", "ima", "img", "bla"}, "blank"),
        ({"blu"}, "blur"),
        ({"fas"}, "fast"),
    ):
        if prefixes & tags:
            return action
    return "skip"


def _cue(match, label: str, number: int) -> VideoSkipCue:
    start = _seconds(match[1])
    end = _seconds(match[3])
    if start < 0 or end <= start:
        raise ValueError("Cue range must be nonnegative and increasing")
    label_region = re.search(r"\[[^\]]+\]", label)
    region = match[2] or (label_region[0] if label_region else None)
    comments = re.findall(r"\(([^()]*)\)", label)
    comment = " ".join(c.strip() for c in comments if c.strip()) or None
    if comment and "-->" in comment:
        comment = None
    keywords = re.sub(r"\([^()]*\)|\[[^\]]*\]|\"[^\"]*\"", " ", label)
    words = re.findall(r"[a-z]+|\d+", keywords.casefold(), flags=re.ASCII)
    prefixes = [word[:3] for word in words]
    category = next((word for word in prefixes if word in _SUBCATEGORIES), None)
    grade = next((word for word in words if word.isascii() and word.isdigit()), None)
    return VideoSkipCue(
        number, start, end, label, _action(set(prefixes), label),
        _SUBCATEGORIES.get(category), _SEVERITIES.get(grade), category is not None,
        comment, region,
    )


def _read_offsets(line: str) -> dict[str, float]:
    value = json.loads(line)
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError("Expected a platform offset object")
    offsets = {}
    for platform, seconds in value.items():
        if (not platform or len(platform) > 120 or not platform.isprintable()
                or type(seconds) not in {int, float} or abs(seconds) > 1_000_000
                or not math.isfinite(seconds)):
            raise ValueError("Invalid platform or offset")
        offsets[platform] = seconds
    return offsets


def _read_sync_line(sync: VideoSkipSync, line: str) -> None:
    if line.startswith("{"):
        sync.platform_offsets_seconds.update(_read_offsets(line))
    elif match := _SCREENSHOT.fullmatch(line):
        payload = base64.b64decode(match[2], validate=True)
        if not payload:
            raise ValueError("Empty reference image")
        sync.screenshot_media_type = match[1].lower().replace(" ", "/")
        sync.screenshot_sha256 = sha256(payload).hexdigest()
    elif line.casefold().startswith("data:image"):
        raise ValueError("Invalid reference image")
    elif len(line) > MAX_TEXT:
        raise ValueError("VideoSkip metadata line is too long")
    elif len(sync.notes) < 128:
        sync.notes.append(line)


def _is_label(line: str) -> bool:
    return bool(
        line and not line.startswith(("{", "data:image", "WEBVTT"))
        and not _POINT.fullmatch(line) and not _TIME_RANGE.fullmatch(line)
        and not re.match(r"^\s*-?\d+:\d+.*-->", line)
    )


def parse_videoskip(content: bytes) -> ParsedVideoSkip:
    """Parse a bounded .skp, retaining unsupported actions and line-level issues."""
    if len(content) > MAX_CONTENT_BYTES:
        raise VideoSkipParseError("videoskip_too_large", "VideoSkip file exceeds the size limit")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise VideoSkipParseError("invalid_videoskip_file", "VideoSkip file must be UTF-8") from exc
    if "\x00" in decoded:
        raise VideoSkipParseError("invalid_videoskip_file", "VideoSkip file contains invalid text")

    lines = decoded.splitlines()
    if len(lines) > MAX_LINES:
        raise VideoSkipParseError("videoskip_too_large", "VideoSkip line limit exceeded")
    first = next((index for index, line in enumerate(lines) if line.strip()), len(lines))
    sync = VideoSkipSync()
    cues = []
    issues = []
    anchor_pending = False
    cue_count = 0
    index = first
    while index < len(lines):
        number = index + 1
        current = lines[index].strip()
        index += 1
        if not current:
            continue
        if index - 1 == first and "-->" not in current and (anchor := _POINT.fullmatch(current)):
            try:
                sync.reference_seconds = _seconds(anchor[1])
                sync.reference_description = anchor[2][:MAX_TEXT] if anchor[2] else None
                anchor_pending = not sync.reference_description
            except ValueError:
                issues.append(VideoSkipImportIssue(
                    code="invalid_sync_reference", line_number=number,
                    message="Reference timestamp is invalid",
                ))
            continue
        if match := _TIME_RANGE.fullmatch(current):
            cue_count += 1
            anchor_pending = False
            if cue_count > MAX_CUES:
                raise VideoSkipParseError(
                    "videoskip_too_large", "VideoSkip cue limit exceeded", number
                )
            label = match[4]
            if not label and index < len(lines) and _is_label(lines[index].strip()):
                label = lines[index].strip()
                index += 1
            try:
                if len(label or "") > MAX_TEXT:
                    raise ValueError("VideoSkip cue label is too long")
                cue = _cue(match, label or "", number)
                cues.append(cue)
            except ValueError as exc:
                issues.append(VideoSkipImportIssue(
                    code="invalid_cue_range", line_number=number, message=str(exc)
                ))
            else:
                if cue.to_filter() is None:
                    code, message = (
                        ("unsupported_action", f"VideoSkip {cue.source_action} was omitted")
                        if cue.source_action not in {"skip", "mute"}
                        else ("unknown_category", "VideoSkip category is missing or unrecognized")
                    )
                    issues.append(VideoSkipImportIssue(
                        code=code, line_number=number, message=message
                    ))
        elif "-->" in current and not current.startswith(("{", "data:image")):
            anchor_pending = False
            issues.append(VideoSkipImportIssue(
                code="invalid_cue_range", line_number=number,
                message="VideoSkip cue timing is malformed",
            ))
        elif anchor_pending and _is_label(current):
            sync.reference_description = current[:MAX_TEXT]
            anchor_pending = False
        else:
            anchor_pending = False
            try:
                _read_sync_line(sync, current)
            except (ValueError, binascii.Error, RecursionError, OverflowError):
                issues.append(VideoSkipImportIssue(
                    code="invalid_sync_metadata", line_number=number,
                    message="VideoSkip synchronization metadata could not be read",
                ))
    return ParsedVideoSkip(sha256(content).hexdigest(), sync, cues, issues)
