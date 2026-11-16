"""Read the archive: media items from removed sources with their scans, reviews, and exports."""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

from sqlalchemy.orm import Session, selectinload

from scanner.persistence.archive import archived_reads
from scanner.persistence.rows.db_archive import ArchivedSource
from scanner.persistence.rows.db_auth import UserProfileRow, UserRow
from scanner.persistence.rows.db_catalog import MediaItemRow
from scanner.persistence.rows.db_reviews import SavedReviewRow
from scanner.persistence.rows.db_scans import MediaItemExportRow, ScanRow


@dataclass(frozen=True)
class ArchivedScan:
    id: str
    scan_number: int
    name: str | None
    status: str
    created_at: datetime


@dataclass(frozen=True)
class ArchivedReview:
    id: str
    name: str
    owner: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ArchivedExport:
    id: int
    format: str
    scope: str
    exported_at: datetime


@dataclass(frozen=True)
class ArchivedItem:
    id: str
    source: ArchivedSource
    title: str
    item_type: str
    series: str | None
    season: int | None
    episode: int | None
    last_episode: int | None
    path: str | None
    scans: list[ArchivedScan]
    reviews: list[ArchivedReview]
    exports: list[ArchivedExport]


@dataclass(frozen=True)
class ArchivePage:
    total: int
    items: list[ArchivedItem]


def _owner_names(db: Session, reviews: list[SavedReviewRow]) -> dict[int, str | None]:
    ids = {review.owner_id for review in reviews if review.owner_id is not None}
    if not ids:
        return {}
    rows = (
        db.query(UserRow.id, UserProfileRow.display_name, UserRow.username)
        .outerjoin(UserProfileRow, UserProfileRow.user_id == UserRow.id)
        .filter(UserRow.id.in_(ids))
    )
    return {user_id: display or username for user_id, display, username in rows}


class EpisodeNumbering(NamedTuple):
    series: str | None
    season: int | None
    episode: int | None
    last_episode: int | None


_NONE = EpisodeNumbering(None, None, None, None)
# `SxxEyy`, optionally ending a multi-episode range as `SxxEyyEzz` or `SxxEyy-Ezz`.
_EPISODE = re.compile(
    r"(?<![a-z0-9])s(\d{1,4})[ ._-]?e(\d{1,4})((?:-?e\d{1,4})*)(?!\d)", re.IGNORECASE
)
_SEASON_FOLDER = re.compile(r"season[ ._-]*\d+|specials", re.IGNORECASE)
_DRIVE = re.compile(r"[a-z]:", re.IGNORECASE)


def _folders(path: str) -> list[str]:
    """The folders of `path` below its root; a drive or a UNC server and share is the root."""
    parts = [part for part in re.split(r"[\\/]", path) if part]
    if re.match(r"[\\/]{2}[^\\/]", path):
        parts = parts[2:]
    elif parts and _DRIVE.fullmatch(parts[0]):
        parts = parts[1:]
    return parts[:-1]


def episode_from_path(path: str | None) -> EpisodeNumbering:
    """Series and episode numbers that a Sonarr file path states; anything it does not is None.

    The series is the folder above a `Season N` or `Specials` folder, else the file name's
    text before ` - SxxEyy`. A guess from any other folder would often name a library.
    """
    if not path:
        return _NONE
    name = re.split(r"[\\/]", path)[-1]
    folders = _folders(path)
    match = _EPISODE.search(name)
    series = None
    if len(folders) >= 2 and _SEASON_FOLDER.fullmatch(folders[-1]):
        series = folders[-2]
    elif match is not None and name[: match.start()].endswith(" - "):
        series = name[: match.start() - 3].strip() or None
    if match is None:
        return EpisodeNumbering(series, None, None, None)
    episode = int(match[2])
    tail = [int(number) for number in re.findall(r"\d+", match[3])]
    last = tail[-1] if tail and tail[-1] > episode else None
    return EpisodeNumbering(series, int(match[1]), episode, last)


def _episode(
    item: MediaItemRow, scans: list[ScanRow], reviews: list[SavedReviewRow]
) -> EpisodeNumbering:
    """Prefer numbers an MCF document recorded, then the file path; the series comes from paths."""
    if item.item_type != "Episode":
        return _NONE
    found = [episode_from_path(path) for path in [item.path, *(scan.source_path for scan in scans)]]
    series = next((each.series for each in found if each.series), None)
    numbered = next((each for each in found if each.episode is not None), _NONE)
    documents = [review.mcf_document for review in reviews] + [scan.mcf_document for scan in scans]
    for document in documents:
        if document is not None and document.episode is not None:
            same = (numbered.season, numbered.episode) == (document.season, document.episode)
            last = numbered.last_episode if same else None
            return EpisodeNumbering(series, document.season, document.episode, last)
    return numbered._replace(series=series)


def _by_item(rows) -> defaultdict[str, list]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.media_item_id].append(row)
    return grouped


def read_archive(db: Session, *, with_paths: bool, offset: int, limit: int) -> ArchivePage:
    """One page of archived media items, newest first.

    `with_paths` includes server paths and the series read from them.
    """
    with archived_reads(db):
        archived = db.query(MediaItemRow).filter(MediaItemRow.archived_source.is_not(None))
        total = archived.count()
        items = (
            archived.order_by(MediaItemRow.updated_at.desc(), MediaItemRow.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
        ids = [item.id for item in items]
        scans = _by_item(
            db.query(ScanRow)
            .options(selectinload(ScanRow.mcf_document))
            .filter(ScanRow.media_item_id.in_(ids))
            .order_by(ScanRow.scan_number)
        )
        review_rows = (
            db.query(SavedReviewRow)
            .options(selectinload(SavedReviewRow.mcf_document))
            .filter(SavedReviewRow.media_item_id.in_(ids))
            .order_by(SavedReviewRow.created_at, SavedReviewRow.id)
            .all()
        )
        reviews = _by_item(review_rows)
        exports = _by_item(
            db.query(MediaItemExportRow)
            .filter(MediaItemExportRow.media_item_id.in_(ids))
            .order_by(MediaItemExportRow.exported_at, MediaItemExportRow.id)
        )
        owners = _owner_names(db, review_rows)
    result = []
    for item in items:
        item_scans, item_reviews = scans[item.id], reviews[item.id]
        numbering = _episode(item, item_scans, item_reviews)
        result.append(
            ArchivedItem(
                id=item.id,
                source=ArchivedSource(item.archived_source),
                title=item.name,
                item_type=item.item_type,
                series=numbering.series if with_paths else None,
                season=numbering.season,
                episode=numbering.episode,
                last_episode=numbering.last_episode,
                path=item.path if with_paths else None,
                scans=[
                    ArchivedScan(scan.id, scan.scan_number, scan.name, scan.status, scan.created_at)
                    for scan in item_scans
                ],
                reviews=[
                    ArchivedReview(
                        review.id, review.name, owners.get(review.owner_id), review.updated_at
                    )
                    for review in item_reviews
                ],
                exports=[
                    ArchivedExport(
                        export.id, export.format.value, export.scope.value, export.exported_at
                    )
                    for export in exports[item.id]
                ],
            )
        )
    return ArchivePage(total, result)
