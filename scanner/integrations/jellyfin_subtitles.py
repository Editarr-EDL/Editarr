"""Discover safely mapped subtitle sources for one Jellyfin media version."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from scanner.formats.subtitles import parse_subtitles
from scanner.modeling.model_catalog import JellyfinMediaItem
from scanner.modeling.model_subtitles import SubtitleSource
from scanner.persistence.repository.repository_paths import resolve_local_path
from scanner.pipeline.subtitle_tracks import embedded_subtitle_streams
from scanner.runtime.subtitle_sidecars import generated_name, read_local_bytes

_SUPPORTED = {"srt", "ass", "ssa", "vtt"}
_ALIASES = {
    "subrip": "srt",
    "webvtt": "vtt",
    "mov_text": "srt",
    "tx3g": "srt",
    "hdmv_pgs_subtitle": "pgs",
    "dvd_subtitle": "vobsub",
}
_LANGUAGE = {
    "ara": "ar",
    "ben": "bn",
    "chi": "zh",
    "zho": "zh",
    "cze": "cs",
    "ces": "cs",
    "dan": "da",
    "dut": "nl",
    "nld": "nl",
    "eng": "en",
    "fin": "fi",
    "fre": "fr",
    "fra": "fr",
    "ger": "de",
    "deu": "de",
    "gre": "el",
    "ell": "el",
    "heb": "he",
    "hin": "hi",
    "hun": "hu",
    "ind": "id",
    "ita": "it",
    "jpn": "ja",
    "kor": "ko",
    "nor": "no",
    "pol": "pl",
    "por": "pt",
    "rum": "ro",
    "ron": "ro",
    "rus": "ru",
    "spa": "es",
    "swe": "sv",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "vie": "vi",
}
_EMBEDDED_FORMATS = {
    "srt": ("srt", "copy"),
    "ass": ("ass", "copy"),
    "ssa": ("ass", "copy"),
    "vtt": ("webvtt", "copy"),
    "mov_text": ("srt", "srt"),
    "tx3g": ("srt", "srt"),
}
_EMBEDDABLE = {
    ".mkv",
    ".mp4",
    ".m4v",
    ".mov",
    ".webm",
    ".avi",
    ".mpg",
    ".mpeg",
    ".m2ts",
    ".ts",
    ".flv",
    ".wmv",
    ".ogm",
    ".vob",
}


@dataclass(frozen=True)
class JellyfinSubtitle:
    source: SubtitleSource
    path: Path | None = None
    stream_index: int | None = None
    container: str | None = None
    encoder: str | None = None


def subtitle_id(version: str, kind: str, identifier: str) -> str:
    """Bind a client-selectable identifier to an exact Jellyfin source."""
    return hashlib.sha256(f"{version}\0{kind}\0{identifier}".encode()).hexdigest()[:32]


def normalize_language(language: object) -> str | None:
    """Normalize the usual Jellyfin and FFmpeg ISO codes without inventing a language."""
    if not isinstance(language, str):
        return None
    value = language.strip().lower().replace("_", "-")
    if value in {"und", "unk"} or not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", value):
        return None
    base, _, region = value.partition("-")
    normalized = _LANGUAGE.get(base, base)
    return f"{normalized}-{region}" if region else normalized


def _version_streams(item: JellyfinMediaItem, version: str | None, path: str):
    selected = next((value for value in item.versions if value.id == version), None)
    if selected and selected.subtitle_streams:
        return selected.subtitle_streams
    if path == item.path:
        return [stream for stream in item.media_streams if stream.get("Type") == "Subtitle"]
    return []


def _external_path(db, media: Path, name: object) -> Path | None:
    if not isinstance(name, str) or not name:
        return None
    try:
        mapped = Path(resolve_local_path(db, name).local_path)
        if not mapped.is_absolute():
            return None
        candidate = mapped.parent.resolve(strict=True) / mapped.name
        if candidate.parent == media.parent and candidate.name.casefold().startswith(
            f"{media.stem.casefold()}."
        ):
            return candidate
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def _usable_subtitle(path: Path | None, format: str) -> bool:
    if path is None or format not in _SUPPORTED:
        return False
    try:
        return bool(parse_subtitles(read_local_bytes(path), format).cues)
    except (OSError, ValueError):
        return False


def jellyfin_sibling_stems(db, item: JellyfinMediaItem, media: Path) -> frozenset[str]:
    """Find the other catalog video stems Jellyfin can match in this media directory."""
    stems = set()
    paths = {item.path, *(version.path for version in item.versions)}
    for path in paths - {None, ""}:
        try:
            mapped = Path(resolve_local_path(db, path).local_path)
            other = mapped.parent.resolve() / mapped.name
        except (OSError, ValueError, RuntimeError):
            continue
        if other != media and other.parent == media.parent:
            stems.add(other.stem.casefold())
    return frozenset(stems)


def _external_identity(db, media, stream: dict, sibling_stems: set[str], published: set[str]):
    path = _external_path(db, media, stream.get("Path"))
    if path:
        name = path.name.casefold()
        if (
            name in published
            or generated_name(path.name)
            or any(name.startswith(stem + ".") for stem in sibling_stems)
        ):
            return None
    format = path.suffix.lower().lstrip(".") if path else _format(stream)[0]
    identifier = path.name if path else f"unavailable:{stream.get('Index')}"
    return identifier, format, path, _usable_subtitle(path, format)


def _format(stream: dict) -> tuple[str, str]:
    codec = stream.get("Codec")
    codec = (
        codec.lower()
        if isinstance(codec, str) and re.fullmatch(r"[A-Za-z0-9_]{1,40}", codec)
        else "unknown"
    )
    format = _ALIASES.get(codec, codec)
    return format, "srt" if codec == "subrip" else "vtt" if codec == "webvtt" else codec


def discover_jellyfin_subtitles(
    db,
    *,
    item: JellyfinMediaItem,
    version: str | None,
    server_path: str,
    media: Path,
    audio_language: str | None,
    published: set[str],
    catalog_sibling_stems: frozenset[str],
) -> list[JellyfinSubtitle]:
    """Only offer Jellyfin streams of the already authorized physical version."""
    discovered = {}
    published = {filename.casefold() for filename in published}
    external = [
        stream
        for stream in _version_streams(item, version, server_path)[:128]
        if stream.get("IsExternal") is True
    ]
    embedded = embedded_subtitle_streams(media) if media.suffix.lower() in _EMBEDDABLE else []
    media_prefix = f"{media.stem.casefold()}."
    sibling_stems = {stem for stem in catalog_sibling_stems if stem.startswith(media_prefix)}
    for stream in (*external, *embedded):
        language = normalize_language(stream.get("Language"))
        index = stream.get("Index")
        if stream.get("IsExternal") is True:
            found = _external_identity(db, media, stream, sibling_stems, published)
            if found is None:
                continue
            identifier, format, path, supported = found
            kind = "external"
            container, encoder = None, None
        else:
            if type(index) is not int or not 0 <= index < 128:
                continue
            identifier, path, kind = str(index), None, "embedded"
            format, codec = _format(stream)
            container, encoder = _EMBEDDED_FORMATS.get(codec, (None, None))
            supported = container is not None and media.suffix.lower() in _EMBEDDABLE
        identity = subtitle_id(version or "", kind, identifier)
        forced = stream.get("IsForced") is True
        sdh = stream.get("IsHearingImpaired") is True
        label = stream.get("DisplayTitle") or stream.get("Title")
        server_subtitle_path = stream.get("Path")
        if (
            not isinstance(label, str)
            or not label.strip()
            or (
                isinstance(server_subtitle_path, str)
                and server_subtitle_path
                and server_subtitle_path in label
            )
        ):
            label = f"{language or 'Unknown language'} subtitle"
        label = " ".join(label.split())[:128]
        source = SubtitleSource(
            id=identity,
            label=label,
            language=language,
            format=format,
            kind=kind,
            forced=forced,
            sdh=sdh,
            supported=supported,
            recommended=bool(
                supported
                and audio_language
                and language
                and audio_language.partition("-")[0] == language.partition("-")[0]
            ),
        )
        discovered[identity] = JellyfinSubtitle(source, path, index, container, encoder)
    return sorted(
        discovered.values(),
        key=lambda item: (
            not item.source.recommended,
            not item.source.supported,
            item.source.forced,
            item.source.label.casefold(),
            item.source.id,
        ),
    )
