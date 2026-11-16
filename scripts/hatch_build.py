"""Validate the staged production frontend before Hatch creates an artifact."""

import json
import sys
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_assets import source_identity, validate_frontend, validate_release_versions


class CustomBuildHook(BuildHookInterface):
    """Require RP-10's staged asset layout for wheels and source distributions."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        if version == "editable":
            return
        validate_release_versions(Path(self.root))
        revision, dirty = source_identity(Path(self.root))
        self._metadata_directory = tempfile.TemporaryDirectory(prefix="editarr-build-info-")
        metadata = Path(self._metadata_directory.name) / "build-info.json"
        metadata.write_text(
            json.dumps({"source_revision": revision, "source_dirty": dirty}) + "\n",
            encoding="utf-8",
        )
        build_data.setdefault("force_include", {})[str(metadata)] = "scanner/build-info.json"
        helper = Path(self.root) / "scanner" / "backup" / "age_passphrase.exp"
        build_data.setdefault("force_include", {})[str(helper)] = (
            "scanner/backup/age_passphrase.exp"
        )
        frontend = Path(self.root) / "scanner" / "frontend"
        validate_frontend(frontend)
        # The wheel already includes this package directory. The sdist needs an explicit
        # include because the release-only staging path is ignored by Git.
        if self.target_name == "sdist":
            build_data.setdefault("force_include", {})[str(frontend)] = "scanner/frontend"
            build_data.setdefault("force_include", {})[
                str(Path(self.root) / "web" / "package.json")
            ] = "web/package.json"

    def finalize(self, version: str, build_data: dict[str, object], artifact_path: str) -> None:
        if hasattr(self, "_metadata_directory"):
            self._metadata_directory.cleanup()
