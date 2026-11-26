"""Title and identity for one user-owned subtitle choice on a shared media item."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from scanner.modeling.model_subtitles import SubtitleSource

_PLAYBACK_FLAGS = ("forced", "foreign", "default")
_ACCESSIBILITY_FLAGS = {"cc", "hi", "sdh"}
_LEGACY_LANGUAGE_ALIASES = {"pob", "pb", "pop", "es-mx"}
_ILLEGAL_FILENAME = frozenset('<>:"/\\|?*')
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?")
_OUTPUT_FORMATS = {"srt", "ass", "ssa", "vtt"}
_FILENAME_BYTES = 255

# Jellyfin's NamingOptions video extensions across 10.11 and 12.
_VIDEO_EXTENSIONS = frozenset(
    """
    .001 .3g2 .3gp .amv .asf .asx .avi .bin .bivx .divx .dv .dvr-ms .f4v .fli .flv
    .ifo .img .iso .m2t .m2ts .m2v .m4v .mkv .mk3d .mov .mp4 .mpe .mpeg .mpg .mts
    .mxf .nrg .nsv .nuv .ogg .ogm .ogv .pva .qt .rec .rm .rmvb .strm .svq3 .tp .ts
    .ty .viv .vob .vp3 .webm .wmv .wtv .xvid
""".split()
)


class SubtitleNameError(ValueError):
    """An input cannot be represented faithfully by a Jellyfin sidecar name."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class NamedSubtitlePublication:
    publisher: str
    review: str
    version: str
    title: str

    def identity(self, source_id: str) -> list[str]:
        return [self.publisher, self.review, self.version, source_id]


def subtitle_publisher_id(local_export_id: str) -> str:
    """Give ownership records an opaque key independent of account or provider renames."""
    return sha256(f"editarr-subtitle-publisher:{local_export_id}".encode()).hexdigest()


def named_track_title(username: str, review_title: str) -> str:
    """Validate each label while preserving its exact spelling in the player."""
    for value, field in ((username, "Jellyfin username"), (review_title, "saved review title")):
        if not value.strip() or any(
            char in _ILLEGAL_FILENAME or unicodedata.category(char) in {"Cc", "Cs"}
            for char in value
        ):
            raise SubtitleNameError(
                "invalid_subtitle_title",
                f"Change the {field} so it can be used in a subtitle filename.",
            )
        flags = value.casefold()
        if any(word in flags for word in _PLAYBACK_FLAGS):
            raise SubtitleNameError(
                "invalid_subtitle_title",
                f"Change the {field}. Jellyfin treats default, forced, and foreign "
                "as playback flags.",
            )
    title = f"{username} - {review_title}"
    if any(part.casefold() in _ACCESSIBILITY_FLAGS for part in title.split(".")):
        raise SubtitleNameError(
            "invalid_subtitle_title",
            "Change the Jellyfin username or saved review title. Jellyfin treats cc, hi, and sdh "
            "between periods as subtitle flags.",
        )
    return title


def named_publication_path(
    media: Path,
    source: SubtitleSource,
    publication: NamedSubtitlePublication,
    language_tokens: frozenset[str],
) -> Path:
    """Build a Jellyfin sidecar with a literal title plus real language and playback flags."""
    if source.format not in _OUTPUT_FORMATS:
        raise SubtitleNameError(
            "subtitle_source_unavailable", "Choose a supported subtitle source."
        )
    language = source.language
    if language and (not _LANGUAGE.fullmatch(language) or language not in language_tokens):
        raise SubtitleNameError(
            "subtitle_language_unavailable",
            "Jellyfin does not recognize this subtitle's language tag. Choose another source "
            "to preserve the title and language.",
        )
    if language is None or language.partition("-")[0] in {"hi", "hin"}:
        languages = language_tokens | _LEGACY_LANGUAGE_ALIASES
        if any(part.casefold() in languages for part in publication.title.split(".")):
            # When the subtitle has no language or carries Hindi, Jellyfin can
            # consume another language token from the title itself.
            raise SubtitleNameError(
                "invalid_subtitle_title",
                "Change the Jellyfin username or saved review title. Jellyfin reads one of its "
                "period-separated parts as a language for this subtitle source.",
            )
    suffix = [language] if language is not None else []
    if source.forced:
        suffix.append("forced")
    if source.sdh:
        suffix.append("sdh")
    filename = ".".join([media.stem, publication.title, *suffix, source.format])
    if len(filename.encode("utf-8")) > _FILENAME_BYTES:
        raise SubtitleNameError(
            "invalid_subtitle_title",
            "Shorten the saved review title or Jellyfin username. The complete subtitle filename "
            "is too long.",
        )
    return media.with_name(filename)


def require_unique_video_match(
    media: Path,
    destination: Path,
    entries: Iterable[Path],
    catalog_sibling_stems: frozenset[str],
) -> None:
    """Reject another video with an exact or dot-delimited match to the complete sidecar stem."""
    stem = destination.stem.casefold()

    def matches(sibling: str) -> bool:
        return stem == sibling or stem.startswith(sibling + ".")

    if any(matches(sibling) for sibling in catalog_sibling_stems) or any(
        entry != media
        and entry.suffix.casefold() in _VIDEO_EXTENSIONS
        and matches(entry.stem.casefold())
        and entry.is_file()
        for entry in entries
    ):
        raise SubtitleNameError(
            "subtitle_version_ambiguous",
            "Jellyfin would attach the subtitle to more than one video in this folder. "
            "Use a distinct saved review title or distinct video filenames, then preview again.",
        )
