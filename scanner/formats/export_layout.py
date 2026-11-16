"""Canonical export identities and the personal/default EDL and MCF sidecar layout.

This is a cross-repository contract. The Jellyfin plugin consumes the same rules
through tests/fixtures/multi-user/export-layout-v1/manifest.json. For media
`<dir>/<stem>.<ext>` the layout is:

- personal: `<dir>/<format>/<export-id>/<stem>.user-<export-id>.<format>`
- item default: `<dir>/<format>/<stem>.<format>`
- legacy `<dir>/<stem>.<format>` is preserved on disk but never selected.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import PurePath
from typing import TypeVar

from scanner.models import ExportFormat

EXPORT_LAYOUT_VERSION = "editarr-export-layout-1"
LOCAL_EXPORT_PREFIX = "local-"

_CANONICAL_HEX = re.compile(r"[0-9a-f]{32}")
_JELLYFIN_INPUT = re.compile(
    r"[0-9A-Fa-f]{32}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
)
_NIL = "0" * 32

PathT = TypeVar("PathT", bound=PurePath)


class ExportLayoutError(ValueError):
    """A machine-readable refusal; `code` is stable across repositories."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class JellyfinUserId:
    """A Jellyfin user GUID as 32 lowercase hex digits, the plugin's `Guid.ToString("N")`."""

    value: str

    def __post_init__(self) -> None:
        if not _CANONICAL_HEX.fullmatch(self.value) or self.value == _NIL:
            raise ExportLayoutError("invalid_jellyfin_user_id", "Jellyfin user ID is invalid")


@dataclass(frozen=True)
class LocalExportId:
    """An immutable random ID for a local-only account, never derived from a username."""

    value: str

    def __post_init__(self) -> None:
        digits = self.value.removeprefix(LOCAL_EXPORT_PREFIX)
        if (
            not self.value.startswith(LOCAL_EXPORT_PREFIX)
            or not _CANONICAL_HEX.fullmatch(digits)
            or digits == _NIL
        ):
            raise ExportLayoutError("invalid_local_export_id", "Local export ID is invalid")


ExportId = JellyfinUserId | LocalExportId


def parse_jellyfin_user_id(raw: str) -> JellyfinUserId:
    """Normalize a provider GUID in N or D form, in any case, to canonical form.

    Braces, whitespace, and other GUID spellings are rejected rather than repaired.
    """

    if not _JELLYFIN_INPUT.fullmatch(raw):
        raise ExportLayoutError("invalid_jellyfin_user_id", "Jellyfin user ID is invalid")
    return JellyfinUserId(raw.replace("-", "").lower())


def parse_export_id(segment: str) -> ExportId:
    """Parse a stored or path-segment export ID, which must already be canonical."""

    if segment.startswith(LOCAL_EXPORT_PREFIX):
        return LocalExportId(segment)
    return JellyfinUserId(segment)


def new_local_export_id() -> LocalExportId:
    """Allocate a fresh local export ID. Callers must persist it and reject collisions."""

    return LocalExportId(LOCAL_EXPORT_PREFIX + secrets.token_hex(16))


def media_stem(media_name: str) -> str:
    """Return the basename without its final extension, identically in Python and C#.

    A name with no dot keeps its whole name. A name whose only dot is first, or
    whose final extension is empty, is refused because the two runtimes disagree.
    """

    if not media_name or "/" in media_name or "\\" in media_name or "\0" in media_name:
        raise ExportLayoutError("unsupported_media_name", "Media file name cannot be exported")
    dot = media_name.rfind(".")
    if dot == -1:
        return media_name
    if dot == 0 or dot == len(media_name) - 1:
        raise ExportLayoutError("unsupported_media_name", "Media file name cannot be exported")
    return media_name[:dot]


def personal_export_path(media: PathT, export_id: ExportId, export_format: ExportFormat) -> PathT:
    """Return the personal sidecar path for one export ID beside the media file."""

    suffix = export_format.value
    name = f"{media_stem(media.name)}.user-{export_id.value}.{suffix}"
    return media.parent / suffix / export_id.value / name


def default_export_path(media: PathT, export_format: ExportFormat) -> PathT:
    """Return the item-default sidecar path whose existence enables fallback."""

    suffix = export_format.value
    return media.parent / suffix / f"{media_stem(media.name)}.{suffix}"


def legacy_export_path(media: PathT, export_format: ExportFormat) -> PathT:
    """Return the legacy adjacent sidecar, which is preserved but never selected."""

    return media.parent / f"{media_stem(media.name)}.{export_format.value}"
