"""Map scan cues only across continuously matched playback intervals."""

import math
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import groupby

from .model_version_reuse import ReuseAnchor

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_SHINGLE = 5
_MAX_WORDS = 150_000
_MAX_DIALOGUE_GAP = 75.0


@dataclass(frozen=True)
class Alignment:
    segments: tuple[tuple[ReuseAnchor, ...], ...]
    audio_matches: dict[tuple[float, float], tuple[float, float]] = field(default_factory=dict)

    @property
    def anchors(self) -> list[ReuseAnchor]:
        return [anchor for segment in self.segments for anchor in segment]

    @classmethod
    def from_anchors(cls, anchors: Iterable[ReuseAnchor]) -> "Alignment":
        """Validate that each continuous interval and its destination are ordered."""
        ordered = sorted(anchors, key=lambda anchor: anchor.source_seconds)
        segments = tuple(
            tuple(group) for _, group in groupby(ordered, lambda anchor: anchor.segment)
        )
        if len({group[0].segment for group in segments}) != len(segments):
            raise ValueError("Each continuous segment must use a unique segment number")
        if any(len(group) < 2 for group in segments):
            raise ValueError("Add a start and end anchor for each continuous segment")
        if any(
            left.source_seconds >= right.source_seconds
            or left.target_seconds >= right.target_seconds
            for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError("Source and destination timing points must increase")
        return cls(segments)

    def project(self, start: float, end: float | None) -> tuple[float, float | None] | None:
        """Project a complete cue within one interval; leave gaps and exterior cues unmapped."""
        last = start if end is None else end
        if not math.isfinite(start) or not math.isfinite(last) or start < 0 or last < start:
            return None
        for segment in self.segments:
            if segment[0].source_seconds <= start <= last <= segment[-1].source_seconds:
                return _interpolate(segment, start), (
                    _interpolate(segment, end) if end is not None else None
                )
        return None


def _interpolate(segment: tuple[ReuseAnchor, ...], time: float) -> float:
    index = bisect_right(segment, time, key=lambda anchor: anchor.source_seconds)
    if index == len(segment):
        return segment[-1].target_seconds
    left, right = segment[index - 1], segment[index]
    portion = (time - left.source_seconds) / (right.source_seconds - left.source_seconds)
    return round(left.target_seconds + portion * (right.target_seconds - left.target_seconds), 6)


@dataclass(frozen=True)
class TimedWord:
    text: str
    start: float
    end: float


def transcript_words(segments: Iterable[dict], offset: float) -> list[TimedWord]:
    """Read actual word clocks from a cached transcript in movie playback time."""
    result = []
    for segment in segments:
        for word in segment.get("words") or ():
            if not isinstance(word, dict) or not isinstance(word.get("word"), str):
                continue
            try:
                start = float(word["start_seconds"]) + offset
                end = float(word["end_seconds"]) + offset
            except (TypeError, ValueError, KeyError):
                continue
            if not 0 <= start < end <= 172800 or (result and start < result[-1].start):
                continue
            normalized = unicodedata.normalize("NFKC", word["word"]).casefold()
            key = "".join(_WORD.findall(normalized))
            if key:
                result.append(TimedWord(key, start, end))
            if len(result) >= _MAX_WORDS:
                raise ValueError("The selected transcript exceeds the supported length")
    return result


def _unique_shingles(words: list[TimedWord]) -> dict[tuple[str, ...], int]:
    positions: dict[tuple[str, ...], int] = {}
    for index in range(len(words) - _SHINGLE + 1):
        group = words[index : index + _SHINGLE]
        if group[-1].end - group[0].start <= 10.0:
            phrase = tuple(word.text for word in group)
            positions[phrase] = -1 if phrase in positions else index
    return positions


def _in_playback_order(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Find the longest increasing set of matches, skipping reordered dialogue."""
    tails: list[int] = []
    links: list[int] = []
    indexes: list[int] = []
    for index, (_, target) in enumerate(pairs):
        position = bisect_left(tails, target)
        links.append(indexes[position - 1] if position else -1)
        if position == len(tails):
            tails.append(target)
            indexes.append(index)
        else:
            tails[position] = target
            indexes[position] = index
    selected = []
    index = indexes[-1] if indexes else -1
    while index != -1:
        selected.append(pairs[index])
        index = links[index]
    return selected[::-1]


def _continuous(previous: TimedWord, source: TimedWord, before: TimedWord, target: TimedWord):
    source_gap = source.start - previous.start
    target_gap = target.start - before.start
    return (
        0 < source_gap <= _MAX_DIALOGUE_GAP
        and 0 < target_gap <= _MAX_DIALOGUE_GAP
        and abs(source_gap - target_gap) <= min(1.25, 0.30 + 0.02 * source_gap)
    )


def _points(source, target, pairs):
    """Build robust contiguous interval edges from multiple unique phrases."""
    groups = []
    group = []
    for first, second in pairs:
        if group and not _continuous(
            source[group[-1][0]], source[first], target[group[-1][1]], target[second]
        ):
            groups.append(group)
            group = []
        group.append((first, second))
    if group:
        groups.append(group)

    for group in groups:
        if len(group) < 2 or group[-1][0] - group[0][0] < _SHINGLE:
            continue
        first, second = group[0]
        points = [(source[first].start, target[second].start)]
        for first, second in group[1:]:
            earlier, _ = points[-1]
            if source[first].start - earlier >= 20:
                points.append((source[first].start, target[second].start))
        first, second = group[-1]
        points.append((source[first].start, target[second].start))
        if len(points) > 2 and points[-1] == points[-2]:
            points.pop()
        yield points


def transcript_anchors(source: list[TimedWord], target: list[TimedWord]) -> list[ReuseAnchor]:
    """Find unique ordered phrase matches, separating gaps and discontinuities."""
    left, right = _unique_shingles(source), _unique_shingles(target)
    candidates = sorted(
        (index, right[key]) for key, index in left.items() if index >= 0 and right.get(key, -1) >= 0
    )
    anchors = [
        ReuseAnchor(source_seconds=first, target_seconds=second, segment=segment)
        for segment, points in enumerate(_points(source, target, _in_playback_order(candidates)))
        for first, second in points
    ]
    if len(anchors) > 4096:
        raise ValueError("The matching transcript contains too many alignment segments")
    return anchors
