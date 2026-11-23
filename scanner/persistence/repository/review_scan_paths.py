"""Resolve review scan evidence against an authorized current media path."""

from pathlib import Path

from scanner import repository as root

from .community_import_targets import matches_target


def review_scan_location(scan, target: Path | None, association) -> tuple[Path, bool]:
    """Use a proven current path, preserving a signed association's explicit target."""
    if association is not None:
        associated = Path(association.resolved_local_path).resolve()
        return associated, (target is None or target == associated) and matches_target(
            association, associated
        )

    original = root._resolved_scan_path(scan)
    selected = target if target is not None else original
    matches = root._scan_matches_physical_version(scan, selected)
    return (selected if matches else original), matches
