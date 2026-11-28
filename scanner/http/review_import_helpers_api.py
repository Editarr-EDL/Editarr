"""Import content validation and target resolution helpers."""

import logging
import unicodedata

from pydantic import ValidationError
from sqlalchemy.orm import Session

from scanner import api as root
from scanner.mcf import (
    McfParseIssue,
)

logger = logging.getLogger(__name__)

JELLYFIN_ITEM_REQUIRED = (
    "This file must belong to a Jellyfin item. Open its Jellyfin item and import the file there."
)


def names_another_source(errors) -> bool:
    """Whether import options failed because their target names a source other than Jellyfin.

    A Jellyfin target that is only malformed, such as one without an `item_id`, stays an
    ordinary validation error.
    """
    return any(
        error.get("loc", ())[-2:] == ("target", "source") and error.get("type") == "literal_error"
        for error in errors
    )


def _refuse_another_source(exc: Exception) -> None:
    if isinstance(exc, ValidationError) and names_another_source(exc.errors()):
        raise root._review_http_error(
            "jellyfin_item_required", JELLYFIN_ITEM_REQUIRED, 400
        ) from exc


def _safe_import_basename(value: str | None, *, format_label: str = "EDL") -> str:
    candidate = (value or "").replace("\\", "/").rsplit("/", 1)[-1]
    if any(unicodedata.category(character).startswith("C") for character in candidate):
        raise ValueError(f"{format_label} filename contains unsupported characters")
    candidate = " ".join(candidate.split()).strip()
    return candidate or "import.edl"


def _safe_mcf_import_basename(value: str | None) -> str:
    candidate = (value or "").replace("\\", "/").rsplit("/", 1)[-1]
    if any(unicodedata.category(character).startswith("C") for character in candidate):
        raise ValueError("MCF filename contains unsupported characters")
    candidate = " ".join(candidate.split()).strip()
    return candidate or "import.mcf"


class _ImportMediaUnavailable(ValueError):
    pass


class _McfImportIssues(ValueError):
    def __init__(self, issues: list[McfParseIssue]):
        super().__init__("MCF import issues require confirmation")
        self.issues = issues


def _import_error_line(content: bytes, line_number: int | None) -> str | None:
    if line_number is None:
        return None
    try:
        lines = content.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return None
    return lines[line_number - 1] if line_number <= len(lines) else None


def _import_target_media(db: Session, target, *, persist: bool = True):
    """Resolve the target media version for an EDL or MCF import."""
    media, selected_id, catalog_document = root._catalog_review_media(
        db,
        item_id=target.item_id,
        media_source_id=target.media_source_id,
        create=True,
        **({"persist": False} if not persist else {}),
    )
    return media, selected_id, catalog_document
