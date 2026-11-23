"""Propose local timing intervals from dialogue snippets and recognizable cue patterns."""

from __future__ import annotations

import math
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from statistics import median

from .model_version_reuse import ReuseAnchor
from .version_alignment import Alignment, TimedWord

_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")
_PHRASE = 5
_MAX_CUES = 240
_MAX_SHIFT = 180.0
_TOLERANCE = 0.45
_MAX_BRIDGE = 75.0


@dataclass(frozen=True)
class AudioCue:
    start: float
    end: float
    label: str = ""
    dialogue: str = ""

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2


@dataclass(frozen=True)
class Landmark:
    source: AudioCue
    target: float

    @property
    def shift(self) -> float:
        return self.target - self.source.midpoint


def _tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return tuple(word.replace("'", "").replace("’", "") for word in _WORD.findall(normalized))


def _dialogue_matches(
    cues: list[AudioCue], words: list[TimedWord], target: list[AudioCue]
) -> list[Landmark]:
    """Anchor a uniquely heard word using a distinctive snippet on the same cue."""
    requests = {}
    for cue in cues:
        label = _tokens(cue.label)
        if len(cue.dialogue) > 2048:
            continue
        dialogue = _tokens(cue.dialogue)
        indexes = [
            index
            for index in range(len(dialogue) - len(label) + 1)
            if label and dialogue[index : index + len(label)] == label
        ]
        if len(dialogue) < _PHRASE:
            continue
        if len(indexes) != 1 or len(label) > _PHRASE:
            if target and len(dialogue) <= 32:
                requests.setdefault(dialogue[:_PHRASE], []).append((cue, 0, 0))
            continue
        index = indexes[0]
        for start in range(
            max(0, index + len(label) - _PHRASE), min(index, len(dialogue) - _PHRASE) + 1
        ):
            requests.setdefault(dialogue[start : start + _PHRASE], []).append(
                (cue, index - start, len(label))
            )

    positions = {}
    for index in range(len(words) - _PHRASE + 1):
        if words[index + _PHRASE - 1].end - words[index].start > 8:
            continue
        key = tuple(entry.text for entry in words[index : index + _PHRASE])
        if key in requests:
            positions[key] = None if key in positions else index
    target_times = [cue.midpoint for cue in target]
    targets: dict[AudioCue, set[float]] = {}
    for key, index in positions.items():
        if index is None:
            continue
        for cue, offset, length in requests[key]:
            first = words[index + offset]
            if length:
                last = words[index + offset + length - 1]
                midpoint = (first.start + last.end) / 2
            else:
                dialogue = _tokens(cue.dialogue)
                window = words[index : index + len(dialogue)]
                if tuple(word.text for word in window) != dialogue:
                    continue
                final = window[-1].end
                lo = bisect_left(target_times, first.start)
                hi = bisect_right(target_times, final)
                if final - first.start > 12 or hi != lo + 1:
                    continue
                midpoint = target_times[lo]
            targets.setdefault(cue, set()).add(midpoint)

    matched = [
        Landmark(cue, next(iter(points))) for cue, points in targets.items() if len(points) == 1
    ]
    usage = Counter(match.target for match in matched)
    ordered = sorted(
        (match for match in matched if usage[match.target] == 1),
        key=lambda match: match.source.midpoint,
    )
    # Require two independently placed, consistent landmarks before offering a proposal.
    credible = set()
    for first, second in pairwise(ordered):
        span = second.source.midpoint - first.source.midpoint
        if span >= 8 and abs(first.shift - second.shift) <= 0.6 + 0.045 * span:
            credible.update((first, second))
    selected = [entry for entry in ordered if entry in credible]
    if any(a.target >= b.target for a, b in pairwise(selected)):
        return []
    return selected


def _pattern_matches(cues: list[AudioCue], target: list[AudioCue]) -> list[Landmark]:
    """Match timing alone only when one offset has distinctive, ordered support."""
    if len(cues) < 4 or len(target) < 4:
        return []
    source = [cue.midpoint for cue in cues]
    right = [cue.midpoint for cue in target]
    offsets = {
        round((point - original) * 4) / 4
        for original in source
        for point in right[
            bisect_left(right, original - _MAX_SHIFT) : bisect_right(right, original + _MAX_SHIFT)
        ]
    }
    quorum = max(4, math.ceil(min(len(source), len(right)) * 0.6))
    best: dict[tuple[tuple[int, int], ...], tuple[float, float]] = {}
    # Shift nearby together; count unique one-to-one matches and reject ambiguous windows.
    for offset in offsets:
        pairs = []
        errors = []
        last = -1
        for index, original in enumerate(source):
            center = original + offset
            lo = bisect_left(right, center - _TOLERANCE)
            hi = bisect_right(right, center + _TOLERANCE)
            if hi == lo + 1 and lo > last:
                pairs.append((index, lo))
                errors.append(abs(right[lo] - center))
                last = lo
        if len(pairs) >= quorum and source[pairs[-1][0]] - source[pairs[0][0]] >= 30:
            key = tuple(pairs)
            score = sum(errors) / len(errors)
            if key not in best or score < best[key][1]:
                best[key] = (offset, score)
    if not best:
        return []
    ranked = sorted(best, key=lambda key: (-len(key), best[key][1], abs(best[key][0]), key))
    winner = ranked[0]
    offset = best[winner][0]
    if any(
        len(other) >= len(winner) - 1 and abs(best[other][0] - offset) > 2 * _TOLERANCE
        for other in ranked[1:]
    ):
        return []
    return [Landmark(cues[first], right[second]) for first, second in winner]


def _points(landmarks: list[Landmark], duration: float) -> list[ReuseAnchor]:
    groups: list[list[Landmark]] = []
    for point in landmarks:
        if not groups:
            groups.append([point])
            continue
        previous = groups[-1][-1]
        gap = point.source.midpoint - previous.source.midpoint
        if gap <= 0 or point.target <= previous.target:
            continue
        if gap <= _MAX_BRIDGE and abs(point.shift - previous.shift) <= 0.4 + 0.02 * gap:
            groups[-1].append(point)
        else:
            groups.append([point])

    result: list[ReuseAnchor] = []
    for segment, group in enumerate(groups):
        first, last = group[0], group[-1]
        start = first.source.start + first.shift
        end = last.source.end + last.shift
        if start < 0 or end > duration:
            continue
        if result and (
            first.source.start <= result[-1].source_seconds or start <= result[-1].target_seconds
        ):
            continue
        points = [(first.source.start, start)]
        points.extend((item.source.midpoint, item.target) for item in group)
        points.append((last.source.end, end))
        result.extend(
            ReuseAnchor(source_seconds=source, target_seconds=target, segment=segment)
            for source, target in points
        )
    return result


def _independent(cues: list[AudioCue]) -> list[AudioCue]:
    """Collapse overlapping detections so a single utterance cannot supply several votes."""
    valid = sorted(
        {
            item
            for item in cues
            if math.isfinite(item.start)
            and math.isfinite(item.end)
            and 0 <= item.start < item.end <= 172800
            and item.end - item.start <= 4
        },
        key=lambda item: (
            item.midpoint,
            -len(item.dialogue),
            item.start,
            item.end,
            item.label,
            item.dialogue,
        ),
    )
    independent = []
    for cue in valid:
        if not independent or cue.start >= independent[-1].end:
            independent.append(cue)
    if len(independent) <= _MAX_CUES:
        return independent
    last = len(independent) - 1
    return [independent[index * last // (_MAX_CUES - 1)] for index in range(_MAX_CUES)]


def cue_anchors(
    source: list[AudioCue], words: list[TimedWord], target: list[AudioCue], duration: float
) -> tuple[list[ReuseAnchor], str | None]:
    """Prefer unique dialogue evidence, then a distinctive pattern of flagged cue times."""
    original = _independent(source)
    target = _independent(target)
    landmarks = _dialogue_matches(original, words, target)
    if landmarks:
        anchors = _points(landmarks, duration)
        if anchors:
            return anchors, "dialogue"
    landmarks = _pattern_matches(original, target)
    anchors = _points(landmarks, duration)
    return anchors, "timing" if anchors else None


def infer_transcript_offset(cues: list[AudioCue], words: list[TimedWord]) -> float | None:
    """Recover a legacy extraction clock from independent flags on that same track."""
    matches = _dialogue_matches(_independent(cues), words, [])
    if len(matches) < 3:
        return None
    offset = median(-match.shift for match in matches)
    inliers = [match for match in matches if abs(match.shift + offset) <= 0.1]
    if len(inliers) >= max(3, math.ceil(len(matches) * 0.8)) and (
        inliers[-1].source.midpoint - inliers[0].source.midpoint >= 30
    ):
        return offset
    return None


def match_unmapped_dialogue(
    cues: list[AudioCue], words: list[TimedWord], alignment: Alignment
) -> dict[tuple[float, float], tuple[float, float]]:
    """Match the flagged phrase and nearby saved words at the established local offset."""
    anchors = alignment.anchors
    if not anchors:
        return {}
    source_times = [anchor.source_seconds for anchor in anchors]
    word_times = [word.start for word in words]
    tokens = [word.text for word in words]
    matches = {}
    for cue in cues:
        if alignment.project(cue.start, cue.end) or not 0 < cue.end - cue.start <= 4:
            continue
        index = bisect_left(source_times, cue.midpoint)
        nearby = anchors[max(0, index - 1) : index + 1]
        if min(abs(anchor.source_seconds - cue.midpoint) for anchor in nearby) > _MAX_BRIDGE:
            continue
        shifts = [anchor.target_seconds - anchor.source_seconds for anchor in nearby]
        if max(shifts) - min(shifts) > 0.4:
            continue
        center = cue.midpoint + median(shifts)
        label = list(_tokens(cue.label))
        dialogue = list(_tokens(cue.dialogue))
        if not label or len(label) > _PHRASE or len(cue.dialogue) > 2048:
            continue
        label_places = [i for i in range(len(dialogue)) if dialogue[i : i + len(label)] == label]
        if len(label_places) != 1:
            continue
        position = label_places[0]
        left = dialogue[max(0, position - 2) : position]
        right = dialogue[position + len(label) : position + len(label) + 2]
        candidates = []
        for first in range(
            bisect_left(word_times, center - 1.5), bisect_right(word_times, center + 0.65)
        ):
            after = first + len(label)
            if tokens[first:after] != label:
                continue
            word_center = (words[first].start + words[after - 1].end) / 2
            if abs(word_center - center) > 0.65:
                continue
            left_matches = (
                len(left) == 2
                and first >= 2
                and tokens[first - 2 : first] == left
                and words[after - 1].end - words[first - 2].start <= 8
            )
            right_matches = (
                len(right) == 2
                and tokens[after : after + 2] == right
                and words[after + 1].end - words[first].start <= 8
            )
            if left_matches or right_matches:
                candidates.append(word_center - cue.midpoint)
        if len(candidates) == 1:
            shift = candidates[0]
            matches[cue.start, cue.end] = (round(cue.start + shift, 6), round(cue.end + shift, 6))
    return matches
