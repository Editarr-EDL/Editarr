"""Advance only audit retention at its service boundary in a run-owned scratch database."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scanner.db import get_session_factory
from scanner.security.audit.store import run_retention
from scanner.settings.config import settings


def main() -> None:
    data = Path(sys.argv[1]).resolve()
    if (
        data != Path(settings.data_dir).resolve()
        or not data.parent.name.startswith("editarr-verify-")
        or not (data / "scanner.db").is_file()
        or (data / "scanner.db").is_symlink()
    ):
        raise SystemExit("Audit retention proof needs the run-owned verification database")
    now = datetime.now(timezone.utc) + timedelta(days=91)
    with get_session_factory()() as db:
        deleted = run_retention(db, now=now)
    print(
        json.dumps(
            {"boundary": "audit.store.run_retention", "days_advanced": 91, "deleted": deleted}
        )
    )


if __name__ == "__main__":
    main()
