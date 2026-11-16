"""List restored transfer and link move records that disagree with the disk.

A backup taken during an account transfer or link can bring back moves whose
files are elsewhere by now (ruling wave14-fresh-install-restore). The report
only reads: MU-38's fail-safe recovery decides what happens to the files, and
nothing here blocks an account.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

RECORDED_PATH_MISSING = "recorded_path_missing"
"""The move's publication records this path, and no file is there."""
UNRECORDED_COPY = "unrecorded_copy"
"""A file sits at this move path, and no publication records it."""

_MOVES = """
SELECT 'account_transfer_moves', move.id, move.user_id, move.export_id, move.old_path,
       move.new_path, move.outcome, publication.destination_path
FROM account_transfer_moves AS move
LEFT JOIN media_item_exports AS publication ON publication.id = move.export_id
UNION ALL
SELECT 'account_link_export_moves', move.id, link.user_id, move.export_id, move.old_path,
       move.new_path, move.outcome, publication.destination_path
FROM account_link_export_moves AS move
JOIN account_link_transitions AS link ON link.id = move.transition_id
LEFT JOIN media_item_exports AS publication ON publication.id = move.export_id
ORDER BY 1, 2
"""


def move_disagreements(database_path: Path) -> list[dict]:
    """Return one entry per move path whose record and disk disagree."""
    uri = f"{Path(database_path).resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        recorded = {
            row[0]
            for row in database.execute(
                "SELECT destination_path FROM media_item_exports WHERE destination_path != ''"
            )
        }
        moves = database.execute(_MOVES).fetchall()
    findings = []
    for table, move_id, user_id, export_id, old_path, new_path, outcome, publication in moves:
        for path in (old_path, new_path):
            if path == publication and not Path(path).is_file():
                finding = RECORDED_PATH_MISSING
            elif (
                path not in recorded
                and (Path(path).exists() or Path(path).is_symlink())
                and not (path == old_path and outcome == "kept")
            ):
                finding = UNRECORDED_COPY
            else:
                continue
            findings.append(
                {
                    "table": table,
                    "move_id": move_id,
                    "user_id": user_id,
                    "export_id": export_id,
                    "finding": finding,
                    "path": path,
                }
            )
    return findings
