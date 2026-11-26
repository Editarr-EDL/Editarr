"""Subtitle sidecars with no-clobber creation and recoverable ownership."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from scanner.formats.subtitles import MAX_SUBTITLE_BYTES
from scanner.modeling.model_subtitles import PublishedSubtitle, SubtitleSource

from .durability import atomic_write_bytes, sync_directory
from .maintenance import _lock, _unlock
from .subtitle_publication_names import NamedSubtitlePublication, require_unique_video_match

_PUBLISHED = "Editarr Filtered"
_HASH = re.compile(r"[0-9a-f]{64}")


class SubtitleConflict(ValueError):
    """The destination could not be verified as Editarr's own output."""


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def media_slot(media: Path, version: str) -> str:
    """Identify this local version without writing its path to ownership records."""
    return _digest(f"{media.name}\0{version}".encode())


def file_fingerprint(path: Path) -> tuple[int, ...]:
    """Only a local regular file can be a subtitle input, output, or ownership record."""
    state = path.stat(follow_symlinks=False)
    return _fingerprint(state)


def _fingerprint(state: os.stat_result) -> tuple[int, ...]:
    if not stat.S_ISREG(state.st_mode):
        raise ValueError("Expected a regular file")
    return state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns


def read_local_bytes(path: Path, limit: int = MAX_SUBTITLE_BYTES) -> bytes:
    """Bound local input reads and refuse symlinks, FIFOs, and changed files."""
    before = file_fingerprint(path)
    if before[1] > limit:
        raise ValueError("Subtitle input is too large")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if _fingerprint(os.fstat(stream.fileno())) != before:
            raise ValueError("Subtitle input changed")
        content = stream.read(limit + 1)
        if _fingerprint(os.fstat(stream.fileno())) != before or len(content) > limit:
            raise ValueError("Subtitle input changed")
    if file_fingerprint(path) != before:
        raise ValueError("Subtitle input changed")
    return content


def _filename(media: Path, source: SubtitleSource) -> str:
    flags = [f"{_PUBLISHED} {source.id[:16]}"]
    if source.language and re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", source.language):
        flags.append(source.language)
    if source.forced:
        flags.append("forced")
    if source.sdh:
        flags.append("sdh")
    return ".".join([media.stem, *flags, source.format])


def publication_path(media: Path, source: SubtitleSource) -> Path:
    """Use Jellyfin's name, language, and disposition suffixes for the selected version."""
    return media.parent / _filename(media, source)


def generated_name(name: str) -> bool:
    """Recognize our reserved output name when Jellyfin discovers it as a source."""
    return bool(re.search(r"\.Editarr Filtered [0-9a-f]{16}\.", name))


def _record_path(destination: Path) -> Path:
    return destination.with_name(f".editarr-subtitle-{_digest(destination.name.encode())}.json")


def _identity(fingerprint: tuple[int, ...], content: bytes) -> dict:
    return {"inode": [fingerprint[0]], "digest": _digest(content)}


def _valid_identity(entry) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("digest"), str)
        and _HASH.fullmatch(entry["digest"]) is not None
        and isinstance(entry.get("inode"), list)
        and len(entry["inode"]) in (1, 2)
        and all(type(value) is int and value >= 0 for value in entry["inode"])
    )


def _read_record(destination: Path, slot: str):
    try:
        record = json.loads(read_local_bytes(_record_path(destination), 8192))
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as exc:
        raise SubtitleConflict("Subtitle publication ownership could not be verified") from exc
    if (
        not isinstance(record, dict)
        or record.get("filename") != destination.name
        or record.get("media_slot") != slot
        or not isinstance(record.get("inputs"), list)
        or not 1 <= len(record["inputs"]) <= 2
        or not all(_valid_identity(value) for value in record["inputs"])
    ):
        raise SubtitleConflict("Subtitle publication ownership could not be verified")
    identity = record.get("publication")
    if "publication" in record and (
        not isinstance(identity, list)
        or len(identity) != 4
        or not all(isinstance(value, str) and 0 < len(value) <= 128 for value in identity)
        or _HASH.fullmatch(identity[0]) is None
    ):
        raise SubtitleConflict("Subtitle publication ownership could not be verified")
    for entry in record["inputs"]:
        entry["inode"] = entry["inode"][-1:]
    return record


def _existing_file(path: Path) -> tuple[tuple[int, ...], dict]:
    fingerprint = file_fingerprint(path)
    content = read_local_bytes(path)
    if file_fingerprint(path) != fingerprint:
        raise SubtitleConflict("The existing subtitle changed")
    return fingerprint, _identity(fingerprint, content)


def _publication_records(media: Path, slot: str):
    """Find the records of the version's managed sidecar paths."""
    for path in sorted(media.parent.iterdir()):
        if not path.name.startswith(f"{media.stem}.") or path.suffix not in {
            ".srt",
            ".ass",
            ".ssa",
            ".vtt",
        }:
            continue
        try:
            record = _read_record(path, slot)
            if record is None:
                continue
            if "publication" not in record and not generated_name(path.name):
                continue
        except (ValueError, OSError):
            continue
        yield path, record


def discover_publications(
    media: Path, slot: str, *, viewer: str | None = None
) -> list[PublishedSubtitle]:
    """List verified publications for the selected media file."""
    return discover_publications_and_owned_sources(media, slot, viewer=viewer)[0]


def discover_publications_and_owned_sources(
    media: Path,
    slot: str,
    *,
    viewer: str | None = None,
    publication: NamedSubtitlePublication | None = None,
) -> tuple[list[PublishedSubtitle], dict[str, str]]:
    """List verified publications and the selected review's own filename per source."""
    discovered = []
    own_sources = {}
    for path, record in _publication_records(media, slot):
        try:
            if _existing_file(path)[1] not in record["inputs"]:
                continue
            identity = record.get("publication")
            owned = bool(viewer and identity and identity[0] == viewer)
            discovered.append(PublishedSubtitle.model_validate(record | {"owned_by_viewer": owned}))
            if (
                publication is not None
                and identity
                and identity == publication.identity(identity[3])
            ):
                if record["label"] == publication.title or identity[3] not in own_sources:
                    own_sources[identity[3]] = path.name
        except (ValueError, OSError):
            continue
    return discovered, own_sources


def _stage(directory: Path, payload: bytes, *, shared: bool = False) -> Path:
    fd, name = tempfile.mkstemp(dir=directory, prefix=".editarr-subtitle-", suffix=".tmp")
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            if shared:
                os.chmod(path, 0o644)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _save_record(path: Path, record: dict) -> None:
    atomic_write_bytes(
        path,
        json.dumps(record).encode(),
        prefix=".editarr-subtitle-",
        suffix=".tmp",
        sync_dir=True,
    )


@contextmanager
def _destination_lock(record_path: Path):
    """Serialize competing instances that can both write this media directory."""
    path = record_path.with_suffix(".lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags, 0o600)
    acquired = False
    try:
        opened = _fingerprint(os.fstat(descriptor))
        acquired = _lock(descriptor, exclusive=True, blocking=False)
        if not acquired:
            raise SubtitleConflict("This subtitle is already being published. Try again.")
        if file_fingerprint(path)[0] != opened[0]:
            raise SubtitleConflict("The subtitle publication lock changed")
        yield
    finally:
        if acquired:
            _unlock(descriptor)
        os.close(descriptor)


def _publish_locked(
    destination: Path,
    slot: str,
    source: SubtitleSource,
    content: bytes,
    publication: NamedSubtitlePublication | None = None,
) -> bool:
    record_path = _record_path(destination)
    previous = _read_record(destination, slot)
    identity = publication.identity(source.id) if publication else None
    if previous is not None and previous.get("publication") != identity:
        raise SubtitleConflict("A different subtitle publication already uses this filename")
    try:
        before, old_file = _existing_file(destination)
    except FileNotFoundError:
        before = old_file = None
    except (ValueError, OSError) as exc:
        raise SubtitleConflict("The existing subtitle destination could not be verified") from exc

    if before is not None and (previous is None or old_file not in previous["inputs"]):
        raise SubtitleConflict("A different subtitle file already exists at the destination")

    temporary = _stage(destination.parent, content, shared=True)
    try:
        new_file = _identity(file_fingerprint(temporary), content)
        record = {
            "filename": destination.name,
            "label": publication.title if publication else f"{source.label} · Filtered",
            "format": source.format,
            "forced": source.forced,
            "media_slot": slot,
            "inputs": [new_file] if old_file is None else [old_file, new_file],
        }
        if publication is not None:
            record["publication"] = identity
        # If interrupted, an exact inode and digest pair still proves either owned output.
        _save_record(record_path, record)
        if before is None:
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise SubtitleConflict("A subtitle appeared at the destination") from exc
        else:
            if file_fingerprint(destination) != before:
                raise SubtitleConflict("The existing subtitle changed before it could be replaced")
            os.replace(temporary, destination)
        sync_directory(destination.parent)
        record["inputs"] = [new_file]
        _save_record(record_path, record)
    finally:
        temporary.unlink(missing_ok=True)
    return before is not None


def publish_sidecar(media: Path, slot: str, source: SubtitleSource, content: bytes) -> bool:
    """Write a legacy shared sidecar without replacing an unverified output."""
    if len(content) > MAX_SUBTITLE_BYTES:
        raise ValueError("The resulting subtitle is too large")
    destination = publication_path(media, source)
    with _destination_lock(_record_path(destination)):
        return _publish_locked(destination, slot, source, content)


def _name_key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def check_named_destination(
    media: Path,
    destination: Path,
    slot: str,
    publication: NamedSubtitlePublication,
    source_id: str,
    *,
    catalog_sibling_stems: frozenset[str] = frozenset(),
) -> None:
    """Refuse a name shared with another video or an existing file owned by someone else."""
    key = _name_key(destination.name)
    entries = tuple(destination.parent.iterdir())
    require_unique_video_match(media, destination, entries, catalog_sibling_stems)
    if any(entry.name != destination.name and _name_key(entry.name) == key for entry in entries):
        raise SubtitleConflict(
            "A subtitle already uses this filename on a case-insensitive match. "
            "Choose a distinct saved review title."
        )
    previous = _read_record(destination, slot)
    if previous is not None and previous.get("publication") != publication.identity(source_id):
        raise SubtitleConflict(
            "A different subtitle publication already uses this filename. "
            "Choose a distinct saved review title."
        )
    try:
        _state, current = _existing_file(destination)
    except FileNotFoundError:
        return
    except (ValueError, OSError) as exc:
        raise SubtitleConflict("The existing subtitle destination could not be verified") from exc
    if previous is None or current not in previous["inputs"]:
        raise SubtitleConflict(
            "A different subtitle file already exists at the destination. "
            "Choose a distinct saved review title."
        )


def publish_named_sidecar(
    media: Path,
    slot: str,
    source: SubtitleSource,
    content: bytes,
    publication: NamedSubtitlePublication,
    destination: Path,
    *,
    catalog_sibling_stems: frozenset[str] = frozenset(),
) -> tuple[str, bool]:
    """Write one named output and retire previous names only after the new output is safe."""
    if len(content) > MAX_SUBTITLE_BYTES:
        raise ValueError("The resulting subtitle is too large")
    if destination.parent != media.parent or not destination.name.startswith(media.stem + "."):
        raise ValueError("The subtitle destination does not belong to the selected media file")
    identity = publication.identity(source.id)
    # Serialize logical replacements as well as title collisions across Editarr processes
    # using the same media directory, including different instances with separate databases.
    with _destination_lock(media.parent / ".editarr-subtitle-publications.json"):
        check_named_destination(
            media,
            destination,
            slot,
            publication,
            source.id,
            catalog_sibling_stems=catalog_sibling_stems,
        )
        previous_names = []
        for path, record in _publication_records(media, slot):
            if path == destination or record.get("publication") != identity:
                continue
            before, actual = _existing_file(path)
            if actual not in record["inputs"]:
                raise SubtitleConflict("The previous named subtitle changed before replacement")
            previous_names.append((path, before))
        with _destination_lock(_record_path(destination)):
            replaced = _publish_locked(destination, slot, source, content, publication)
        for path, before in previous_names:
            if file_fingerprint(path) != before:
                raise SubtitleConflict("The previous named subtitle changed before replacement")
            path.unlink()
            _record_path(path).unlink(missing_ok=True)
            sync_directory(path.parent)
    return destination.name, replaced or bool(previous_names)
