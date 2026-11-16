"""Keep archived media out of every ORM read except the archive's own.

A media item with `archived_source` is archived with its scans, reviews, exports,
and community import targets. Every ORM SELECT on any session hides them, so live
code never sees an archived row. Code inside `archived_reads(db)` sees them.
Bulk updates, Core table selects, and plain connections bypass that guard and
filter with `live_only` instead.

`scanner.db` imports this module for its side effect: the listener below must be
registered before any session runs a query.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import ColumnElement, event, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from .rows.db_catalog import MediaItemRow
from .rows.db_community import CommunityImportTargetRow
from .rows.db_reviews import SavedReviewRow
from .rows.db_scans import MediaItemExportRow, ScanRow

_ARCHIVED_READS = "archived_reads"
_media = MediaItemRow.__table__
_ARCHIVED_IDS = select(_media.c.id).where(_media.c.archived_source.is_not(None))
_CHILDREN = (ScanRow, SavedReviewRow, MediaItemExportRow, CommunityImportTargetRow)


def live_only(row: type) -> ColumnElement[bool]:
    """A WHERE clause that keeps `row`'s archived records out of a statement the guard skips."""
    if row is MediaItemRow:
        return _media.c.archived_source.is_(None)
    if row not in _CHILDREN:
        raise TypeError(f"{row.__name__} is not archived with its media item")
    return row.__table__.c.media_item_id.not_in(_ARCHIVED_IDS)


def names_archived_item(db: Session, item_id: str) -> bool:
    """Whether `item_id` is the ID or source ID of an archived media item."""
    found = select(_media.c.id).where(
        _media.c.archived_source.is_not(None),
        (_media.c.id == item_id) | (_media.c.jellyfin_id == item_id),
    )
    return db.execute(found.limit(1)).first() is not None


def is_archived(db: Session, row: type, record_id: str) -> bool:
    """Whether `row`'s record `record_id` belongs to an archived media item."""
    table = row.__table__
    found = select(table.c.id).where(table.c.id == record_id, ~live_only(row))
    return db.execute(found.limit(1)).first() is not None


@contextmanager
def archived_reads(db: Session) -> Iterator[Session]:
    """Let ORM reads on `db` return archived rows until the block exits."""
    db.info[_ARCHIVED_READS] = True
    try:
        yield db
    finally:
        db.info.pop(_ARCHIVED_READS, None)


@event.listens_for(Session, "do_orm_execute")
def _hide_archived(state: ORMExecuteState) -> None:
    if not state.is_select or state.session.info.get(_ARCHIVED_READS):
        return
    state.statement = state.statement.options(
        with_loader_criteria(
            MediaItemRow, lambda item: item.archived_source.is_(None), include_aliases=True
        ),
        *(
            with_loader_criteria(
                row, lambda child: child.media_item_id.not_in(_ARCHIVED_IDS), include_aliases=True
            )
            for row in _CHILDREN
        ),
    )
