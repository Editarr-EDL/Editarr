"""Independent release, scanner and compatibility identities for this binary."""

import json
import re
import sys
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel

from scanner import __version__
from scanner.persistence.migrations.adoption import HEAD_REVISION

SCANNER_BEHAVIOR_VERSION = "scanner-1"
LOCAL_API_VERSION = "1"


class VersionInformation(BaseModel):
    release: str
    build_revision: str | None
    build_dirty: bool | None
    scanner_behavior: str
    database_schema: str
    local_api: str
    community_api: str | None
    artifact_schema: str | None


def version_information() -> VersionInformation:
    """Read embedded build metadata; never consult Git or current environment at runtime."""
    metadata = None
    if getattr(sys, "frozen", False):
        path = Path(sys.executable).parent / "editarr-runtime-inventory.json"
        if path.is_file():
            metadata = json.loads(path.read_text(encoding="utf-8"))
    else:
        resource = files("scanner").joinpath("build-info.json")
        if resource.is_file():
            metadata = json.loads(resource.read_text(encoding="utf-8"))
    metadata = metadata or {}
    revision = metadata.get("source_revision")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        revision = None
    dirty = metadata.get("source_dirty")
    return VersionInformation(
        release=__version__,
        build_revision=revision,
        build_dirty=dirty if isinstance(dirty, bool) else None,
        scanner_behavior=SCANNER_BEHAVIOR_VERSION,
        database_schema=HEAD_REVISION,
        local_api=LOCAL_API_VERSION,
        community_api=None,
        artifact_schema=None,
    )
