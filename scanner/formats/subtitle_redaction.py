"""Match confirmed language mutes to unique timed text without changing its styling."""

import html
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from .subtitles import SubtitleCue, SubtitleDocument

_MARKUP = re.compile(r"\{[^{}]*}|<[^<>]*>|&(?:#[xX]?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);|\\[Nnh]")
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_PUNCTUATION_ONLY = " \t\r\n.,!?;:'\"…—-"
_PREVIEW_LIMIT = 12


@dataclass(frozen=True)
class SubtitleMute:
    start: float
    end: float
    expression: str


def _marker_text(marker: str) -> str:
    if marker.startswith("&"):
        return html.unescape(marker)
    if marker.startswith("\\"):
        return " " if marker == r"\h" else "\n"
    return ""


def _project(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Build visible text and an offset map, retaining styles in the raw source."""
    visible, offsets = [], []
    index = 0
    for match in _MARKUP.finditer(text):
        visible.append(text[index : match.start()])
        offsets.extend((i, i + 1) for i in range(index, match.start()))
        value = _marker_text(match.group())
        visible.append(value)
        offsets.extend([(match.start(), match.end())] * len(value))
        index = match.end()
    visible.append(text[index:])
    offsets.extend((i, i + 1) for i in range(index, len(text)))
    return "".join(visible), offsets


def plain_subtitle_text(text: str) -> str:
    """Convert styled text to a plain preview value without interpreting it as HTML."""
    return _MARKUP.sub(lambda match: _marker_text(match.group()), text).strip()


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_normalize(word[0]) for word in _WORD.finditer(text))


def _normalize(token: str) -> str:
    return unicodedata.normalize("NFKC", token).replace("’", "'").casefold()


@dataclass(frozen=True)
class _CueWords:
    visible: str
    offsets: list[tuple[int, int]]
    matches: tuple[re.Match, ...]
    tokens: tuple[str, ...]
    timings: tuple[tuple[float, float], ...]

    @classmethod
    def from_cue(cls, cue: SubtitleCue):
        visible, offsets = _project(cue.text)
        matches = tuple(_WORD.finditer(visible))
        tokens = tuple(_normalize(word[0]) for word in matches)
        timed = [(token, start, end) for word, start, end in cue.words for token in _tokens(word)]
        timings = (
            tuple((start, end) for _, start, end in timed)
            if tuple(token for token, _, _ in timed) == tokens
            else ()
        )
        return cls(visible, offsets, matches, tokens, timings)

    def matching_positions(self, phrase: tuple[str, ...], mute: SubtitleMute):
        for index in range(len(self.tokens) - len(phrase) + 1):
            if self.tokens[index : index + len(phrase)] != phrase:
                continue
            last = index + len(phrase) - 1
            if any(
                self.visible[
                    self.matches[previous].end() : self.matches[previous + 1].start()
                ].strip()
                for previous in range(index, last)
            ):
                continue
            if self.timings and (
                self.timings[index][0] >= mute.end or self.timings[last][1] <= mute.start
            ):
                continue
            yield self.matches[index].start(), self.matches[last].end()

    def remove(self, raw: str, spans: set[tuple[int, int]]) -> str | None:
        remove = set()
        for start, end in spans:
            # A comma or colon attached to the muted phrase generally should go with it.
            tail = re.match(r"[,;:]?[ \t\u00a0]+", self.visible[end:])
            if tail:
                end += tail.end()
            else:
                while start and self.visible[start - 1] in " \t\u00a0":
                    start -= 1
            for raw_start, raw_end in self.offsets[start:end]:
                remove.update(range(raw_start, raw_end))
        result = "".join(char for index, char in enumerate(raw) if index not in remove)
        return result if plain_subtitle_text(result).strip(_PUNCTUATION_ONLY) else None


@dataclass(frozen=True)
class SubtitleRedaction:
    document: SubtitleDocument
    replacements: dict[int, str | None]
    overlapping: frozenset[int]

    def preview(self) -> list[dict[str, str | float]]:
        """Return a small sample favoring edited and overlapping untouched cues."""

        def cue_order(index):
            return self.document.cues[index].start, index

        edited = sorted(self.replacements, key=cue_order)[: _PREVIEW_LIMIT - 3]
        untouched = sorted(self.overlapping - self.replacements.keys(), key=cue_order)
        chosen = (edited + untouched)[:_PREVIEW_LIMIT]
        if not chosen:
            chosen = list(range(min(3, len(self.document.cues))))
        result = []
        for index in sorted(chosen, key=cue_order):
            cue = self.document.cues[index]
            output = self.replacements.get(index, cue.text)
            result.append(
                {
                    "start_seconds": cue.start,
                    "end_seconds": cue.end,
                    "original": plain_subtitle_text(cue.text)[:1024],
                    "filtered": plain_subtitle_text(output or "")[:1024],
                }
            )
        return result


def redact_subtitles(
    document: SubtitleDocument, mutes: tuple[SubtitleMute, ...]
) -> SubtitleRedaction:
    """Remove only unique literal word sequences among cues overlapping each mute."""
    timeline = sorted(enumerate(document.cues), key=lambda row: row[1].start)
    starts = [cue.start for _, cue in timeline]
    latest_ends = []
    for _, cue in timeline:
        latest_ends.append(max(cue.end, latest_ends[-1]) if latest_ends else cue.end)

    @lru_cache(maxsize=64)
    def cue_words(index):
        return _CueWords.from_cue(document.cues[index])

    removed = defaultdict(set)
    overlapping = set()
    for mute in mutes:
        phrase = _tokens(mute.expression)
        candidates = []
        first = bisect_right(latest_ends, mute.start)
        last = bisect_left(starts, mute.end)
        for index, cue in timeline[first:last]:
            if cue.end <= mute.start:
                continue
            overlapping.add(index)
            if not phrase or len(candidates) > 1:
                continue
            for span in cue_words(index).matching_positions(phrase, mute):
                candidates.append((index, span))
                if len(candidates) > 1:
                    break
        if len(candidates) == 1:
            index, span = candidates[0]
            removed[index].add(span)
    replacements = {
        index: cue_words(index).remove(document.cues[index].text, spans)
        for index, spans in removed.items()
    }
    return SubtitleRedaction(document, replacements, frozenset(overlapping))
