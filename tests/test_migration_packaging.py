"""A lightweight installed package must retain runnable migration resources."""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

from native_build.pyinstaller_collect import MIGRATIONS, required_packages


def test_migration_resources_work_from_an_isolated_wheel(tmp_path):
    root = Path(__file__).parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    targets = metadata["tool"]["hatch"]["build"]["targets"]
    assert "scanner" in targets["wheel"]["packages"]
    assert "/scanner" in targets["sdist"]["include"]
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['hatchling==1.32.0']\n"
        "build-backend = 'hatchling.build'\n"
        "[project]\nname = 'editarr-migration-fixture'\nversion = '0.0.1'\n"
        "[tool.hatch.build.targets.wheel]\npackages = ['scanner']\n"
        "[tool.hatch.build.targets.sdist]\ninclude = ['/scanner', '/pyproject.toml']\n",
        encoding="utf-8",
    )
    scanner = project / "scanner"
    scanner.mkdir()
    shutil.copyfile(root / "scanner/__init__.py", scanner / "__init__.py")
    persistence = scanner / "persistence"
    persistence.mkdir()
    shutil.copyfile(root / "scanner/persistence/__init__.py", persistence / "__init__.py")
    shutil.copytree(
        MIGRATIONS, persistence / "migrations", ignore=shutil.ignore_patterns("__pycache__")
    )
    runtime = scanner / "runtime"
    runtime.mkdir()
    for name in ("__init__.py", "maintenance.py", "runtime_paths.py"):
        shutil.copyfile(root / "scanner/runtime" / name, runtime / name)

    output = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--sdist", "--out-dir", str(output)],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(output.glob("*.whl"))
    archive = next(output.glob("*.tar.gz"))
    resource = "scanner/persistence/migrations/versions/baseline-v1.json"
    with zipfile.ZipFile(wheel) as package:
        assert resource in package.namelist()
        assert "scanner/persistence/migrations/env.py" in package.namelist()
        assert (
            "scanner/persistence/migrations/versions/0001_current_baseline.py" in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0002_scan_provenance.py" in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0003_local_community.py" in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0004_media_identity.py" in package.namelist()
        )
        assert "scanner/persistence/migrations/versions/0005_audio_tracks.py" in package.namelist()
        assert (
            "scanner/persistence/migrations/versions/0006_community_evidence.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0007_community_publications.py"
            in package.namelist()
        )
        assert "scanner/persistence/migrations/versions/0008_api_key.py" in package.namelist()
        assert (
            "scanner/persistence/migrations/versions/0009_readable_api_key.py" in package.namelist()
        )
        assert "scanner/persistence/migrations/versions/0010_user_accounts.py" in package.namelist()
        assert (
            "scanner/persistence/migrations/versions/0011_activity_ownership.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0012_personal_filter_values.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0013_community_identities.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0014_media_access_exceptions.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0016_personal_filter_rules.py"
            in package.namelist()
        )
        assert "scanner/persistence/migrations/versions/0017_user_profiles.py" in package.namelist()
        assert (
            "scanner/persistence/migrations/versions/0018_jellyfin_account_sync.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0019_review_policy_evidence.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0020_review_scan_repairs.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0021_export_snapshots.py" in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0022_export_regenerations.py"
            in package.namelist()
        )
        assert (
            "scanner/persistence/migrations/versions/0023_account_link_transitions.py"
            in package.namelist()
        )
    with tarfile.open(archive, "r:gz") as package:
        assert any(name.endswith("/" + resource) for name in package.getnames())

    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as package:
        package.extractall(installed)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import scanner; "
            "from scanner.persistence.migrations.runner import migrate_fresh_database; "
            f"assert Path(scanner.__file__).is_relative_to(Path({str(installed)!r})); "
            "migrate_fresh_database(Path('fresh.db'), Path('.')); "
            "import sqlite3; "
            "assert sqlite3.connect('fresh.db').execute("
            "'SELECT version_num FROM alembic_version').fetchone()[0] == "
            "'0037_community_master_sets'",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    for target in ("macos-arm64", "windows-x64", "linux-x86_64"):
        assert "alembic" in required_packages(target)


def test_installed_app_startup_uses_packaged_migrations(tmp_path):
    root = Path(__file__).parents[1]
    project = tmp_path / "project"
    project.mkdir()
    for name in (".gitignore", "pyproject.toml", "README.md"):
        shutil.copyfile(root / name, project / name)
    web = project / "web"
    web.mkdir()
    shutil.copyfile(root / "web/package.json", web / "package.json")
    shutil.copytree(
        root / "scanner", project / "scanner", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(root / "web/dist", project / "scanner/frontend")
    scripts = project / "scripts"
    scripts.mkdir()
    for name in ("hatch_build.py", "release_assets.py"):
        shutil.copyfile(root / "scripts" / name, scripts / name)
    output = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--sdist", "--out-dir", str(output)],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    extracted_source = tmp_path / "source"
    extracted_source.mkdir()
    with tarfile.open(next(output.glob("*.tar.gz"))) as archive:
        assert any(name.endswith("/web/package.json") for name in archive.getnames())
        archive.extractall(extracted_source, filter="data")
    rebuilt = tmp_path / "rebuilt"
    subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(rebuilt)],
        cwd=next(extracted_source.iterdir()),
        check=True,
        capture_output=True,
        text=True,
    )
    frontend = root / "web/dist"
    frontend_files = {
        f"scanner/frontend/{asset.relative_to(frontend).as_posix()}": asset.read_bytes()
        for asset in frontend.rglob("*")
        if asset.is_file()
    }
    for wheel in (next(output.glob("*.whl")), next(rebuilt.glob("*.whl"))):
        with zipfile.ZipFile(wheel) as package:
            names = [name for name in package.namelist() if name.startswith("scanner/frontend/")]
            assert sorted(names) == sorted(frontend_files)
            assert {name: package.read(name) for name in names} == frontend_files
    installed = tmp_path / "installed"
    with zipfile.ZipFile(next(output.glob("*.whl"))) as package:
        required = {
            "scanner/formats/community/codec.py",
            "scanner/settings/version_info.py",
            "scanner/security/community_artifact_signatures.py",
            "scanner/security/community_signatures.py",
            "scanner/security/community_key_rotation.py",
            "scanner/security/community_key_recovery.py",
            "scanner/integrations/community/submissions.py",
            "scanner/integrations/community/publications.py",
            "scanner/integrations/community/masters.py",
            "scanner/integrations/community/master_votes.py",
            "scanner/integrations/community/master_sync.py",
            "scanner/integrations/community/find.py",
            "scanner/backup/age_passphrase.exp",
            "scanner/backup/schedule.py",
            "scanner/backup/schedule_store.py",
            "scanner/backup/schedule_runner.py",
            "scanner/http/setup_restore_api.py",
            "scanner/http/backup_schedule_api.py",
            "scanner/frontend/index.html",
            "scanner/build-info.json",
        }
        names = set(package.namelist())
        assert required <= names
        assert not any(
            name.startswith(("community-service/", "web/operator/", "scanner/frontend/admin/"))
            or "/dist-operator/" in name
            or name.endswith((".key", ".sqlite", "secrets.json", "service.json"))
            for name in names
        )
        metadata = json.loads(package.read("scanner/build-info.json"))
        assert metadata == {"source_revision": None, "source_dirty": None}
        for module in (
            "import_edl",
            "import_mcf",
            "import_metadata",
            "import_transaction",
            "community_import_targets",
            "community_master_links",
            "community_publications",
        ):
            assert f"scanner/persistence/repository/{module}.py" in package.namelist()
        package.extractall(installed)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(installed)
    environment["EDITARR_DATA_DIR"] = str(tmp_path / "data")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import scanner; "
            f"assert Path(scanner.__file__).is_relative_to(Path({str(installed)!r})); "
            "from scanner import repository; "
            "from scanner.persistence.repository import "
            "import_edl, import_mcf, repository_imports; "
            "from scanner.persistence.repository.import_transaction import stage_imported_scan; "
            "assert repository.import_edl_as_saved_review "
            "is import_edl.import_edl_as_saved_review; "
            "assert repository.import_mcf_as_saved_review "
            "is import_mcf.import_mcf_as_saved_review; "
            "assert repository_imports.import_mcf_as_saved_review "
            "is repository.import_mcf_as_saved_review; "
            "assert callable(stage_imported_scan); "
            "from scanner.integrations.community.masters import MasterSets; "
            "from scanner.persistence.repository.community_master_links import apply_master; "
            "assert callable(MasterSets) and callable(apply_master); "
            "from scanner.http.api_lifecycle import startup; startup(); "
            "from scanner.settings.version_info import version_information; "
            "assert version_information().build_revision is None; "
            "import sqlite3; "
            "assert sqlite3.connect('data/scanner.db').execute("
            "'SELECT version_num FROM alembic_version').fetchone()[0] == "
            "'0037_community_master_sets'",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("age", "expect"):
        executable = shutil.which(name)
        if executable:
            (tools / name).symlink_to(executable)
    environment["PATH"] = str(tools) + os.pathsep + os.defpath
    restore_tests = tmp_path / "test_setup_restore_api.py"
    shutil.copyfile(root / "tests/test_setup_restore_api.py", restore_tests)
    support = tmp_path / "tests"
    support.mkdir()
    (support / "__init__.py").touch()
    for name in (
        "community_identity_support.py",
        "community_schema_fixture.py",
        "community_schema_support.py",
        "jellyfin_setup_fake.py",
        "jellyfin_user_boundary.py",
        "test_community_submission_projection.py",
    ):
        shutil.copyfile(root / "tests" / name, support / name)
    users = support / "fixtures" / "multi-user" / "jellyfin-users-v1"
    users.mkdir(parents=True)
    shutil.copyfile(
        root / "tests/fixtures/multi-user/jellyfin-users-v1/manifest.json", users / "manifest.json"
    )
    vectors = support / "fixtures" / "community"
    vectors.mkdir(parents=True)
    for name in ("donation-artifacts-v2.json", "download-receipt-v1.json"):
        shutil.copyfile(root / "tests/fixtures/community" / name, vectors / name)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import shutil, sys; from pathlib import Path; import scanner; "
            f"assert Path(scanner.__file__).is_relative_to(Path({str(installed)!r})); "
            "assert shutil.which('node') is None; import pytest; "
            "sys.exit(pytest.main(['-q', '--import-mode=importlib', "
            f"{str(restore_tests)!r}, "
            f"{str(support / 'test_community_submission_projection.py')!r}]))",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    if shutil.which("age") and shutil.which("expect"):
        assert "skipped" not in result.stdout
