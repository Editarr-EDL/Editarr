"""Timed text subtitle parsing that preserves source formatting outside edited cues."""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass

MAX_SUBTITLE_BYTES = 8 * 1024 * 1024
_MAX_CUE_CHARACTERS = 16 * 1024
_TIMESTAMP = re.compile(r"(\d{1,3}:)?(\d{2}):(\d{2})([,.]|:)(\d{1,3})$")
_CUE_TIMING = re.compile(r"^\s*(\S+)\s+-->\s+(\S+)")
_SECTION = re.compile(r"^\s*\[([^]]+)]\s*$")


@dataclass(frozen=True)
class SubtitleCue:
    start: float
    end: float
    text: str
    body_start: int
    body_end: int
    block_start: int
    block_end: int
    words: tuple[tuple[str, float, float], ...] = ()


@dataclass(frozen=True)
class SubtitleDocument:
    text: str
    encoding: str
    cues: tuple[SubtitleCue, ...]

    def render(self, replacements: dict[int, str | None]) -> bytes:
        """Replace cue text spans or remove their entire blocks when the cue is empty."""
        parts = []
        offset = 0
        for index, cue in enumerate(self.cues):
            if index not in replacements:
                continue
            replacement = replacements[index]
            if replacement is None:
                start, end = cue.block_start, cue.block_end
                replacement = ""
            else:
                start, end = cue.body_start, cue.body_end
            parts.extend((self.text[offset:start], replacement))
            offset = end
        parts.append(self.text[offset:])
        return "".join(parts).encode(self.encoding)


def _time(value: str) -> float:
    match = _TIMESTAMP.fullmatch(value.strip())
    if not match or int(match[2]) > 59 or int(match[3]) > 59:
        raise ValueError("Invalid subtitle timestamp")
    # ASS gives hundredths, SRT/WebVTT use milliseconds.
    return (
        int((match[1] or "0").rstrip(":")) * 3600
        + int(match[2]) * 60
        + int(match[3])
        + int(match[5]) / 10 ** len(match[5])
    )


def _lines(text: str):
    start = 0
    for raw in text.splitlines(keepends=True):
        end = start + len(raw)
        yield start, end, raw.rstrip("\r\n")
        start = end


def _blocks(text: str):
    lines = []
    for line in _lines(text):
        if line[2].strip():
            lines.append(line)
        elif lines:
            yield lines, line[1]
            lines = []
    if lines:
        yield lines, len(text)


def _cues_by_blocks(text: str, *, webvtt: bool):
    for block, block_end in _blocks(text):
        first = block[0][2].strip()
        if webvtt and (
            first == "WEBVTT"
            or first.startswith(("WEBVTT ", "WEBVTT\t", "NOTE ", "NOTE\t"))
            or first in {"NOTE", "STYLE", "REGION"}
        ):
            continue
        timing = next(
            (
                (position, match)
                for position, (_a, _b, line) in enumerate(block[:2])
                if (match := _CUE_TIMING.match(line))
            ),
            None,
        )
        if timing is None:
            raise ValueError("A subtitle block has no cue timing")
        position, match = timing
        if len(block) <= position + 1:
            # Preserve intentionally empty cues in the source document.
            continue
        start, end = _time(match[1]), _time(match[2])
        if end <= start:
            raise ValueError("Subtitle end time must follow its start time")
        body_start = block[position + 1][0]
        body_end = block[-1][0] + len(block[-1][2])
        yield SubtitleCue(
            start, end, text[body_start:body_end], body_start, body_end, block[0][0], block_end
        )


def _ass_cues(text: str):
    in_events = False
    columns = (
        "layer",
        "start",
        "end",
        "style",
        "name",
        "marginl",
        "marginr",
        "marginv",
        "effect",
        "text",
    )
    for start_line, end_line, line in _lines(text):
        if section := _SECTION.fullmatch(line):
            in_events = section[1].casefold() == "events"
        if not in_events or ":" not in line:
            continue
        kind, _, data = line.partition(":")
        if kind.strip().casefold() == "format":
            columns = tuple(value.strip().casefold() for value in data.split(","))
        elif kind.strip().casefold() == "dialogue":
            if columns[-1:] != ("text",) or not {"start", "end"}.issubset(columns):
                raise ValueError("The subtitle has an unsupported ASS event format")
            fields = data.split(",", len(columns) - 1)
            if len(fields) != len(columns):
                raise ValueError("The subtitle has an incomplete ASS event")
            start, end = _time(fields[columns.index("start")]), _time(fields[columns.index("end")])
            if end <= start:
                raise ValueError("Subtitle end time must follow its start time")
            body = fields[-1]
            body_end = start_line + len(line)
            yield SubtitleCue(
                start, end, body, body_end - len(body), body_end, start_line, end_line
            )


def _decode(content: bytes) -> tuple[str, str]:
    if len(content) > MAX_SUBTITLE_BYTES:
        raise ValueError("The subtitle is too large")
    if content.startswith(codecs.BOM_UTF8):
        return content.decode("utf-8-sig"), "utf-8-sig"
    if content.startswith((codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE)):
        encoding = "utf-16-be" if content.startswith(codecs.BOM_UTF16_BE) else "utf-16-le"
        return content.decode(encoding), encoding
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return content.decode("cp1252"), "cp1252"


def parse_subtitles(content: bytes, format: str) -> SubtitleDocument:
    """Parse editable timed cues in SRT, WebVTT, ASS and SSA without a format round-trip."""
    text, encoding = _decode(content)
    if "\x00" in text or format not in {"srt", "vtt", "ass", "ssa"}:
        raise ValueError("The subtitle format is unsupported")
    # Retain the endian-specific UTF-16 BOM in the original document and its rendered bytes.
    offset = int(text.startswith("\ufeff"))
    payload = text[offset:]
    cues = (
        _ass_cues(payload)
        if format in {"ass", "ssa"}
        else _cues_by_blocks(payload, webvtt=format == "vtt")
    )
    if offset:
        cues = (
            SubtitleCue(
                c.start,
                c.end,
                c.text,
                c.body_start + offset,
                c.body_end + offset,
                c.block_start + offset,
                c.block_end + offset,
            )
            for c in cues
        )
    parsed = tuple(cues)
    if any(len(cue.text) > _MAX_CUE_CHARACTERS for cue in parsed):
        raise ValueError("A subtitle cue is too large")
    return SubtitleDocument(text, encoding, parsed)


def srt_time(seconds: float) -> str:
    """Encode playback seconds on the SubRip millisecond grid."""
    hours, remains = divmod(max(0, round(seconds * 1000)), 3_600_000)
    minutes, remains = divmod(remains, 60_000)
    secs, milliseconds = divmod(remains, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"
