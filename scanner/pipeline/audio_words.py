"""Unicode-aware audio word normalization and Allow/Deny defaults."""

import unicodedata

from scanner.models import FindingCreate


def _word_tokens(text: str) -> list[str]:
    """Keep combining marks attached to their letters in non-Latin scripts."""
    normalized = unicodedata.normalize("NFC", text)
    return "".join(
        char if char.isalnum() or unicodedata.category(char).startswith("M") else " "
        for char in normalized
    ).split()


def _normalize_filter_word(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def _normalize_detected_word(value: str) -> str:
    """Strip punctuation and whitespace from a detected word label."""
    return "".join(
        char
        for char in _normalize_filter_word(value)
        if char.isalnum() or unicodedata.category(char).startswith("M")
    )


def effective_audio_filter_words(
    allow_words: list[str] | None,
    deny_words: list[str] | None,
) -> tuple[set[str], set[str]]:
    """Return normalized Allow and Deny words ready for runtime use.

    Allow entries duplicated in Deny are removed only from the runtime Allow
    set, preserving the persisted settings lists as entered by users.
    """
    runtime_deny = {
        _normalize_detected_word(word)
        for word in (deny_words or [])
        if _normalize_detected_word(word)
    }
    runtime_allow = {
        _normalize_detected_word(word)
        for word in (allow_words or [])
        if _normalize_detected_word(word)
    }
    runtime_allow -= runtime_deny
    return runtime_allow, runtime_deny


def apply_audio_allow_defaults(
    findings: list[FindingCreate],
    allow_words: list[str] | None,
    deny_words: list[str] | None,
) -> list[FindingCreate]:
    """Auto-deselect matched Allow words, except words also present in Deny."""
    runtime_allow, _runtime_deny = effective_audio_filter_words(allow_words, deny_words)
    if not runtime_allow:
        return findings

    adjusted: list[FindingCreate] = []
    for finding in findings:
        if (
            finding.source == "audio"
            and finding.label
            and _normalize_detected_word(finding.label) in runtime_allow
        ):
            adjusted.append(finding.model_copy(update={"selected": False}))
        else:
            adjusted.append(finding)
    return adjusted


def _deny_matches_in_segment(text: str, runtime_deny: set[str]) -> list[str]:
    matches: list[str] = []
    seen: set[str] = set()
    for token in _word_tokens(text):
        normalized = _normalize_filter_word(token)
        if normalized in runtime_deny and normalized not in seen:
            seen.add(normalized)
            matches.append(normalized)
    return matches
