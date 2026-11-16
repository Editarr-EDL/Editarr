"""Export the website's API contract without touching an existing installation."""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="editarr-openapi-") as data_dir:
        os.environ["EDITARR_DATA_DIR"] = data_dir
        sys.path.insert(0, str(ROOT))
        from scanner.api import app

        document = app.openapi()
        document.pop("servers", None)
        (ROOT / "website/public/docs/openapi.json").write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
