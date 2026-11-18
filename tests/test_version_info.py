"""Independent runtime version contract and release-build drift checks."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import scanner
from scanner.api import app
from scanner.http.api_system import router
from scanner.settings import version_info
from scanner.settings.version_info import VersionInformation, version_information
from scripts.release_assets import validate_release_versions
from tests.operations_access_support import as_owner


def test_version_endpoint_has_independent_compatibility_values(tmp_path):
    application = FastAPI()
    application.include_router(router)
    as_owner(application, tmp_path).close()
    with TestClient(application) as client:
        response = client.get("/api/system/version")
    assert response.status_code == 200
    version = response.json()
    assert version["release"] == scanner.__version__ == app.version
    assert version["database_schema"] == "0033_scan_worker_timing"
    assert version["scanner_behavior"] != version["release"]
    assert version["local_api"] != version["release"]
    assert version["community_api"] is None
    assert version["artifact_schema"] is None
    assert set(version) == set(VersionInformation.model_fields)


def test_source_checkout_does_not_invent_packaged_build_identity():
    assert version_information().build_revision is None
    assert version_information().build_dirty is None


def test_malformed_embedded_build_identity_is_not_reported(tmp_path, monkeypatch):
    (tmp_path / "build-info.json").write_text(
        json.dumps({"source_revision": "a" * 100_000, "source_dirty": "false"})
    )
    monkeypatch.setattr(version_info, "files", lambda _package: tmp_path)
    version = version_information()
    assert version.build_revision is None
    assert version.build_dirty is None


@pytest.mark.parametrize("drift", ["web", "scanner"])
def test_release_build_rejects_mismatched_versions(tmp_path, drift):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (tmp_path / "web").mkdir()
    (tmp_path / "scanner").mkdir()
    (tmp_path / "web/package.json").write_text(
        json.dumps({"version": "0.2.0" if drift == "web" else "0.1.0"})
    )
    (tmp_path / "scanner/__init__.py").write_text(
        f'__version__ = "{"0.2.0" if drift == "scanner" else "0.1.0"}"\n'
    )
    with pytest.raises(RuntimeError, match="Release version drift"):
        validate_release_versions(tmp_path)


def test_release_build_accepts_current_metadata():
    assert validate_release_versions(Path(__file__).parents[1]) == scanner.__version__


def test_hatch_wheel_refuses_version_drift(tmp_path):
    root = Path(__file__).parents[1]
    for name in ("pyproject.toml", "README.md"):
        shutil.copyfile(root / name, tmp_path / name)
    for directory in ("scripts", "scanner", "web"):
        (tmp_path / directory).mkdir()
    for name in ("hatch_build.py", "release_assets.py"):
        shutil.copyfile(root / "scripts" / name, tmp_path / "scripts" / name)
    shutil.copyfile(root / "scanner/__init__.py", tmp_path / "scanner/__init__.py")
    (tmp_path / "web/package.json").write_text('{"version": "999.0.0"}\n')
    result = subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(tmp_path / "dist")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Release version drift" in result.stderr
