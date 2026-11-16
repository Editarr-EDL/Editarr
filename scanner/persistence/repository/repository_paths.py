"""Repository operations for server-side media path mappings."""

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from scanner.db import PathMappingRow
from scanner.models import PathMapping, PathMappingCreate, PathMappingUpdate, PathResolution
from scanner.server_paths import UnrepresentablePath, UnresolvablePath, parse_server_path

_HOST_PATHS = os.path


def list_path_mappings(db: Session) -> list[PathMapping]:
    rows = db.query(PathMappingRow).order_by(PathMappingRow.sort_order, PathMappingRow.id).all()
    return [_path_mapping_from_row(row) for row in rows]


def create_path_mapping(db: Session, data: PathMappingCreate) -> PathMapping:
    """Validate and append the remote-to-local mapping in ``data``."""
    _validate_path_mapping(data.remote_path, data.local_path)
    max_sort = (
        db.query(PathMappingRow.sort_order).order_by(PathMappingRow.sort_order.desc()).first()
    )
    row = PathMappingRow(
        remote_path=_normalize_remote_path(data.remote_path),
        local_path=str(Path(data.local_path).expanduser()),
        sort_order=(max_sort[0] if max_sort else -1) + 1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _path_mapping_from_row(row)


def update_path_mapping(
    db: Session, mapping_id: int, data: PathMappingUpdate
) -> PathMapping | None:
    """Validate and replace one mapping by its database identifier."""
    row = db.get(PathMappingRow, mapping_id)
    if not row:
        return None
    remote_path = data.remote_path if data.remote_path is not None else row.remote_path
    local_path = data.local_path if data.local_path is not None else row.local_path
    _validate_path_mapping(remote_path, local_path)
    row.remote_path = _normalize_remote_path(remote_path)
    row.local_path = str(Path(local_path).expanduser())
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _path_mapping_from_row(row)


def delete_path_mapping(db: Session, mapping_id: int) -> bool:
    row = db.get(PathMappingRow, mapping_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def reorder_path_mappings(db: Session, mapping_ids: list[int]) -> list[PathMapping]:
    """Apply the supplied mapping order after validating all mapping IDs."""
    rows = db.query(PathMappingRow).order_by(PathMappingRow.sort_order, PathMappingRow.id).all()
    if len(mapping_ids) != len(rows) or len(set(mapping_ids)) != len(mapping_ids):
        raise ValueError("Provide every path mapping exactly once when reordering")
    rows_by_id = {row.id: row for row in rows}
    if set(mapping_ids) != set(rows_by_id):
        raise ValueError("Path mapping reorder does not match the configured mappings")
    for sort_order, mapping_id in enumerate(mapping_ids):
        row = rows_by_id[mapping_id]
        row.sort_order = sort_order
        row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return list_path_mappings(db)


def resolve_local_path(db: Session, remote_path: str) -> PathResolution:
    """Resolve using the longest component-safe remote prefix, then mapping order.

    Raises `UnresolvablePath` for a path of no recognized server form, such
    as a relative path or a stream URL. Raises its `UnrepresentablePath`
    subclass when a name left after the mapped prefix, or any name when no
    mapping applies, holds a separator this host would split on, such as a
    POSIX server name holding `\\` on a Windows host. A mapped prefix is
    replaced whole, so its own names may hold one.
    """
    normalized_remote = _normalize_remote_path(remote_path)
    mappings = list_path_mappings(db)
    matches = [
        mapping
        for mapping in mappings
        if normalized_remote == mapping.remote_path
        or normalized_remote.startswith(f"{mapping.remote_path}/")
    ]
    if not matches:
        _require_host_names(normalized_remote)
        return PathResolution(remote_path=remote_path, local_path=normalized_remote)

    mapping = min(matches, key=lambda item: (-len(item.remote_path), item.sort_order, item.id))
    suffix = normalized_remote[len(mapping.remote_path) :].lstrip("/")
    _require_host_names(suffix)
    return PathResolution(
        remote_path=remote_path,
        local_path=(
            str(Path(mapping.local_path, *suffix.split("/"))) if suffix else mapping.local_path
        ),
        mapping_id=mapping.id,
    )


def _require_host_names(names: str) -> None:
    separators = {_HOST_PATHS.sep, _HOST_PATHS.altsep} - {None, "/"}
    if any(separator in names for separator in separators):
        raise UnrepresentablePath(
            "This host cannot name a file whose server name holds a path separator"
        )


def _path_mapping_from_row(row: PathMappingRow) -> PathMapping:
    return PathMapping(
        id=row.id,
        remote_path=row.remote_path,
        local_path=row.local_path,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_path_mapping(remote_path: str, local_path: str) -> None:
    _normalize_remote_path(remote_path)
    if not Path(local_path).expanduser().is_absolute():
        raise ValueError("Local path must be an absolute path on the Editarr server")


def _normalize_remote_path(value: str) -> str:
    """The remote path's `/` spelling, split by its server form's separator rules."""
    parsed = parse_server_path(value.strip())
    if parsed is None:
        raise UnresolvablePath("Remote path must be an absolute POSIX or Windows path")
    spelling = parsed.spelling
    return spelling if spelling == "/" else spelling.rstrip("/")
