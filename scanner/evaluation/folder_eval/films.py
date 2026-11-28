"""Real-film windows: link films, copy their ground truth, and merge window items."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from .film_labels import DEFAULT_TAG_MAP_PATH, Tag, TagMap
from .film_sources import GROUND_TRUTH_COPY, FilmSourceError, FilmSources
from .inventory import empty_inventory
from .paths import FILMS_ROOT, Layout, resolves_into_excluded, write_json
from .severity import SeverityMap, window_severity

log = logging.getLogger(__name__)

DEFAULT_FILMS_PATH = Path(__file__).with_name("films.json")
HDR_TRANSFERS = frozenset({"smpte2084", "arib-std-b67"})
GROUND_TRUTH_FILES = ("movies.csv", "tag_timestamps.csv", "movie_tags.csv")


def film_folder(title: str, year: int) -> str:
    """Filesystem-safe `Title (Year)` folder name (`:` and `/` become ` -`)."""
    safe = re.sub(r"\s*[:/]\s*", " - ", title).strip()
    return f"{safe} ({year})"


def window_item_id(path: str, start: float) -> str:
    return hashlib.sha1(f"{path}@{start:g}".encode()).hexdigest()[:16]


def is_film_item(item: dict[str, Any]) -> bool:
    return "window" in item


def probe_film(path: Path) -> tuple[float, bool]:
    """Return (duration seconds, HDR transfer) for the first video stream."""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=color_transfer:format=duration", "-of", "json", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=True)
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or [{}]
    return float(payload["format"]["duration"]), streams[0].get("color_transfer") in HDR_TRANSFERS


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def resolve_imdb_id(film: dict[str, Any], movies: list[dict[str, str]]) -> str:
    """Find the movies.csv id by title + year; fail if it disagrees with the config."""
    title = film["title"].casefold()
    matches = [
        row["imdb_id"] for row in movies
        if str(film["year"]) == row["year"]
        and title in {row["name"].casefold(), row["title_main"].casefold()}
    ]
    if len(matches) != 1:
        raise ValueError(f"{film['title']} ({film['year']}): {len(matches)} movies.csv matches")
    configured = film.get("imdb_id")
    if configured and configured != matches[0]:
        raise ValueError(f"{film['title']}: films.json has {configured}, movies.csv {matches[0]}")
    return matches[0]


def film_tags(rows: list[dict[str, str]], imdb_id: str, offset: float) -> list[Tag]:
    return [
        Tag(
            f"{row['category']}/{row['tag_name']}",
            float(row["start_approx"]) + offset,
            float(row["end_approx"]) + offset,
        )
        for row in rows
        if row["imdb_id"] == imdb_id
    ]


def window_items(
    path: str,
    folder: str,
    imdb_id: str,
    duration: float,
    tags: list[Tag],
    tag_map: TagMap,
    *,
    window_s: float = 10.0,
    offsets: tuple[float, ...] = (2.5, 7.5),
    hdr: bool = False,
) -> list[dict[str, Any]]:
    """One inventory item per window, with frames at `offsets` inside each window."""
    items = []
    start = 0.0
    while start + min(offsets) < duration:
        end = min(start + window_s, duration)
        label = tag_map.label_window(start, end, tags)
        identifier = window_item_id(path, start)
        times = [round(start + offset, 3) for offset in offsets if start + offset < duration]
        item: dict[str, Any] = {
            "id": identifier,
            "path": path,
            "folder": folder,
            "kind": "video",
            "duration_s": round(end - start, 3),
            "expected": label.expected,
            "scored": label.scored,
            "rule": "film-tags",
            "window": [start, round(end, 3)],
            "imdb_id": imdb_id,
            "source_tags": label.source_tags,
            "frames": [
                {"n": index, "t": t, "file": f"frames/{identifier}/{index}.jpg"}
                for index, t in enumerate(times)
            ],
        }
        if hdr:
            item["hdr"] = True
        items.append(item)
        start += window_s
    return items


def _link(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() and os.readlink(link) == str(target):
        return
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(target)
    # Drop links left behind when a film's configured file is renamed.
    for sibling in link.parent.iterdir():
        if sibling.is_symlink() and sibling != link:
            sibling.unlink()


def _copy_ground_truth(source: Path, destination: Path, imdb_ids: set[str]) -> None:
    if source.resolve() == destination.resolve():
        return
    if destination.is_symlink():
        raise PermissionError(f"Refusing to copy ground truth into a symlink: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for name in (*GROUND_TRUTH_FILES, "tags.csv"):
        if (destination / name).is_symlink():  # replace a link, never write through it
            (destination / name).unlink()
    for name in GROUND_TRUTH_FILES:
        if not (source / name).is_file():
            continue
        with (source / name).open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            column = header.index("imdb_id")
            rows = [row for row in reader if row[column] in imdb_ids]
        with (destination / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
    if (source / "tags.csv").is_file():
        (destination / "tags.csv").write_bytes((source / "tags.csv").read_bytes())


def build_films(
    layout: Layout,
    sources: FilmSources,
    config_path: Path = DEFAULT_FILMS_PATH,
    tag_map_path: Path = DEFAULT_TAG_MAP_PATH,
) -> dict[str, Any]:
    """Link films, copy their CSV rows, and replace film windows in inventory.json.

    `config_path` lists the films (no paths); `sources` supplies the local film files
    and the ground-truth CSV directory (see `film_sources`).
    """
    config = json.loads(config_path.read_text(encoding="utf-8"))
    tag_map = TagMap.load(tag_map_path)
    severity_map = SeverityMap.load()
    source = sources.ground_truth_dir
    movies = read_rows(source / "movies.csv")
    timestamps = read_rows(source / "tag_timestamps.csv")
    window_s = float(config.get("window_seconds", 10))
    offsets = tuple(float(value) for value in config.get("frame_offsets", [2.5, 7.5]))
    films_dir = layout.data_root / FILMS_ROOT

    # Resolve every film's file first so a bad or missing one fails before anything changes.
    identified = [
        (film, resolve_imdb_id(film, movies), film_folder(film["title"], film["year"]))
        for film in config["films"]
    ]
    sources.check_keys((imdb_id, name) for _film, imdb_id, name in identified)
    resolved = [
        (film, imdb_id, name, sources.file_for(imdb_id, name))
        for film, imdb_id, name in identified
    ]
    for _film, imdb_id, name, file_path in resolved:
        if resolves_into_excluded(layout.data_root, file_path):
            raise FilmSourceError(
                f"{name} ({imdb_id}): {file_path} is in an excluded folder and is never "
                "linked, probed or scored"
            )

    new_items: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    for film, imdb_id, name, file_path in resolved:
        movie = next(row for row in movies if row["imdb_id"] == imdb_id)
        folder = f"{FILMS_ROOT}/{name}"
        relative = f"{folder}/{file_path.name}"
        _link(file_path, layout.data_root / relative)
        duration, hdr = probe_film(file_path)
        tags = film_tags(timestamps, imdb_id, float(film.get("offset_s", 0.0)))
        unmapped = tag_map.unmapped({tag.name for tag in tags})
        if unmapped:
            log.warning("%s: unmapped tags ignored: %s", folder, sorted(unmapped))
        items = window_items(
            relative, folder, imdb_id, duration, tags, tag_map,
            window_s=window_s, offsets=offsets, hdr=hdr,
        )
        for item in items:
            # Uncorrected truth levels (None = unscored); the viewer applies corrections.
            item["expected_severity"] = window_severity(item, None, severity_map)
        new_items.extend(items)
        positives = Counter(key for item in items if item["scored"] for key in item["expected"])
        report[folder] = {
            "imdb_id": imdb_id,
            "duration_s": round(duration, 1),
            "csv_duration_s": int(movie["duration_sec"]),
            "duration_delta_s": round(duration - int(movie["duration_sec"]), 1),
            "hdr": hdr,
            "tags": len(tags),
            "windows": len(items),
            "unscored": sum(not item["scored"] for item in items),
            "positives": dict(sorted(positives.items())),
        }
        log.info("%s: %s", folder, json.dumps(report[folder]))
        if abs(report[folder]["duration_delta_s"]) > 120:
            log.warning("%s: duration differs from movies.csv by >2 min; check offset_s", folder)

    imdb_ids = {entry["imdb_id"] for entry in report.values()}
    _copy_ground_truth(source, films_dir / GROUND_TRUTH_COPY, imdb_ids)
    inventory = layout.load_inventory() if layout.inventory.is_file() else empty_inventory()
    kept = [item for item in inventory["items"] if not is_film_item(item)]
    inventory["items"] = kept + new_items
    inventory["severity_max"] = severity_map.max_level
    write_json(layout.inventory, inventory)
    write_json(films_dir / "films_report.json", report)
    log.info("Inventory: %d clip items + %d film windows", len(kept), len(new_items))
    return report
