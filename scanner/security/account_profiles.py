"""Validation for the editable profile fields every account may carry.

Display name and display language are optional. Language is
a preference only: Editarr ships English text, so English is the one choice
offered until translations exist, and a stored tag never implies one does.
"""

from __future__ import annotations

import unicodedata

SUPPORTED_LANGUAGES = ("en",)
"""Display languages with complete interface text. Add a tag only with its translation."""


class ProfileValueRejected(ValueError):
    """A submitted profile value that can never be stored. `code` names the field rule."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _printable(value: str) -> bool:
    return not any(unicodedata.category(character).startswith("C") for character in value)


def display_name(value: str) -> str:
    """Trim and NFC-normalize a display name of 1 to 100 printable characters."""

    name = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(name) <= 100 or not _printable(name):
        raise ProfileValueRejected(
            "invalid_display_name", "Display name must be 1 to 100 printable characters"
        )
    return name


def language(value: str) -> str:
    """One of `SUPPORTED_LANGUAGES`."""

    if value not in SUPPORTED_LANGUAGES:
        raise ProfileValueRejected("unsupported_language", "Display language is not available")
    return value
