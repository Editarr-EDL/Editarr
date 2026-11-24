"""Align the timed findings of two Qwen language replay reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from unicodedata import normalize

from scanner.models import FindingCreate

_START_DRIFT = 0.06
_POINT_DRIFT = 0.35
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class LanguageReplayRow:
    timestamp_seconds: float
    baseline: FindingCreate | None
    candidate: FindingCreate | None


def _words(label: str | None) -> tuple[str, ...]:
    return tuple(_WORD.findall(normalize("NFKC", label or "").casefold()))


def _contains_phrase(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    shorter, longer = sorted((left, right), key=len)
    length = len(shorter)
    return any(
        longer[start : start + length] == shorter for start in range(len(longer) - length + 1)
    )


def _timing(finding: FindingCreate, padding: float) -> tuple[float, float]:
    start = finding.start_seconds
    if start > 0:
        start += padding
    end = finding.end_seconds
    return start, max(start, end - padding) if end is not None else start


def _match_score(
    left: tuple[float, float],
    right: tuple[float, float],
    left_words: tuple[str, ...],
    right_words: tuple[str, ...],
) -> tuple[int, float, float] | None:
    if not left_words or not right_words:
        return None

    left_start, left_end = left
    right_start, right_end = right
    start_drift = abs(left_start - right_start)
    overlap = min(left_end, right_end) - max(left_start, right_start)
    if left_words == right_words:
        if overlap > 0:
            priority = 0
        elif start_drift <= _START_DRIFT or (
            (left_start == left_end or right_start == right_end) and start_drift <= _POINT_DRIFT
        ):
            priority = 1
        else:
            return None
    elif overlap > 0 and _contains_phrase(left_words, right_words):
        priority = 2
    else:
        return None
    return priority, start_drift, abs(left_end - right_end)


def align_language_replay_findings(
    baseline: list[FindingCreate],
    candidate: list[FindingCreate],
    *,
    baseline_padding_seconds: float,
    candidate_padding_seconds: float,
) -> list[LanguageReplayRow]:
    """Pair findings one to one using source timing without mute padding."""
    baseline = sorted(baseline, key=lambda finding: finding.start_seconds)
    candidate = sorted(candidate, key=lambda finding: finding.start_seconds)
    baseline_words = [_words(finding.label) for finding in baseline]
    candidate_words = [_words(finding.label) for finding in candidate]
    baseline_timing = [_timing(finding, baseline_padding_seconds) for finding in baseline]
    candidate_timing = [_timing(finding, candidate_padding_seconds) for finding in candidate]
    options = []
    for left_index, left in enumerate(baseline_timing):
        last_start = left[1] + _POINT_DRIFT
        for right_index, right in enumerate(candidate_timing):
            if right[0] > last_start:
                break
            score = _match_score(
                left, right, baseline_words[left_index], candidate_words[right_index]
            )
            if score is not None:
                options.append((score, left_index, right_index))

    baseline_matches: dict[int, int] = {}
    matched_candidates: set[int] = set()
    for _, left_index, right_index in sorted(options):
        if left_index not in baseline_matches and right_index not in matched_candidates:
            baseline_matches[left_index] = right_index
            matched_candidates.add(right_index)

    rows = []
    for left_index, left in enumerate(baseline):
        right_index = baseline_matches.get(left_index)
        right = candidate[right_index] if right_index is not None else None
        timestamp = min(left.start_seconds, right.start_seconds) if right else left.start_seconds
        rows.append(LanguageReplayRow(timestamp, left, right))
    rows.extend(
        LanguageReplayRow(right.start_seconds, None, right)
        for right_index, right in enumerate(candidate)
        if right_index not in matched_candidates
    )
    return sorted(rows, key=lambda row: row.timestamp_seconds)
