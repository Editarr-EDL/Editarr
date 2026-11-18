"""Native PyInstaller build-policy tests that do not create a frozen artifact."""

from __future__ import annotations

import importlib.util
import os
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from native_build import pyinstaller_collect


def _load_build_release():
    path = Path("scripts/build_release.py")
    specification = importlib.util.spec_from_file_location("build_release", path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "workflow", sorted(Path(".github/workflows").glob("native*.yml")), ids=lambda path: path.stem
)
def test_native_workflow_python_inputs_exist(workflow):
    workflow_text = workflow.read_text(encoding="utf-8")
    commands = [workflow_text]
    for shell_script in re.findall(r"\bnative_build/[\w./-]+\.sh\b", workflow_text):
        commands.append(Path(shell_script).read_text(encoding="utf-8"))
    commands_text = "\n".join(commands)
    inputs = re.findall(r"\b(?:native_build|scanner|scripts|tests)/[\w./-]+\.py\b", commands_text)
    modules = re.findall(r"\bpython(?:3)? -m ((?:scripts|native_build)\.[\w.]+)\b", commands_text)
    inputs.extend(module.replace(".", "/") + ".py" for module in modules)
    assert inputs
    for source in inputs:
        assert Path(source).is_file(), f"{workflow}: {source}"


@pytest.mark.parametrize(
    ("platform", "marker"),
    [
        ("darwin", "macos-release.marker"),
        ("linux", "source-media-release.marker"),
        ("win32", "source-media-release.marker"),
    ],
)
def test_source_built_media_requires_frozen_marker(monkeypatch, tmp_path, platform, marker):
    from scanner.runtime import distribution

    monkeypatch.setattr(distribution.sys, "platform", platform)
    monkeypatch.setattr(distribution.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(distribution.sys, "frozen", True, raising=False)
    assert not distribution.has_source_built_media()
    (tmp_path / marker).touch()
    assert distribution.has_source_built_media()
    monkeypatch.setattr(distribution.sys, "frozen", False)
    assert not distribution.has_source_built_media()


def test_target_policy_includes_mlx_only_for_macos_arm64():
    macos = pyinstaller_collect.required_packages("macos-arm64")
    windows = pyinstaller_collect.required_packages("windows-x64")
    linux = pyinstaller_collect.required_packages("linux-x86_64")

    assert {"mlx", "mlx_vlm", "mlx_whisper"}.issubset(macos)
    assert not {"mlx", "mlx_vlm", "mlx_whisper"}.intersection(windows)
    assert not {"mlx", "mlx_vlm", "mlx_whisper"}.intersection(linux)
    assert "ctranslate2" in windows
    assert "ctranslate2" in linux
    assert "torch.cuda" in pyinstaller_collect.excluded_modules("windows-x64")
    assert "nvidia" in pyinstaller_collect.excluded_modules("linux-x86_64")


def test_target_policy_rejects_cross_platform_and_unknown_hosts():
    assert pyinstaller_collect.target_id(system="Darwin", machine="arm64") == "macos-arm64"
    assert pyinstaller_collect.target_id(system="Windows", machine="AMD64") == "windows-x64"
    assert pyinstaller_collect.target_id(system="Linux", machine="x86_64") == "linux-x86_64"
    with pytest.raises(RuntimeError, match="unsupported host"):
        pyinstaller_collect.target_id(system="Darwin", machine="x86_64")
    with pytest.raises(ValueError, match="Unsupported"):
        pyinstaller_collect.required_packages("macos-x64")


def test_collection_filter_keeps_venv_package_data_but_rejects_checkout_state(tmp_path):
    package_data = Path(".venv/lib/python3.11/site-packages/example/data.json")
    assert not pyinstaller_collect._is_forbidden_source(package_data)
    assert pyinstaller_collect._is_forbidden_source(Path("data") / "scanner.db")


def test_native_inventory_records_frontend_helpers_and_exclusions(tmp_path, monkeypatch):
    release = _load_build_release()
    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    payload = runtime / "Editarr"
    payload.write_bytes(b"frozen")
    helpers = runtime / "_internal" / "helpers"
    helpers.mkdir(parents=True)
    ffmpeg = helpers / "ffmpeg"
    ffprobe = helpers / "ffprobe"
    backup_age = helpers / "backup-age"
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")
    backup_age.write_bytes(b"backup-age")
    monkeypatch.setattr(release, "_git_revision", lambda: "a" * 40)

    inventory = release._inventory(
        runtime=runtime,
        target="macos-arm64",
        frontend_hashes={"index.html": "b" * 64},
        packages={"PyInstaller": "6.22.2"},
        helpers={
            "ffmpeg": tmp_path / "build-ffmpeg",
            "ffprobe": tmp_path / "build-ffprobe",
            "backup-age": tmp_path / "build-backup-age",
        },
    )

    assert inventory["target"] == "macos-arm64"
    assert inventory["version"] == release._project_version()
    assert inventory["frontend_sha256"] == {"index.html": "b" * 64}
    assert inventory["resources_root"] == "_internal"
    assert inventory["helpers"]["ffmpeg"]["path"] == "_internal/helpers/ffmpeg"
    assert inventory["helpers"]["backup-age"]["path"] == "_internal/helpers/backup-age"
    assert "coverage" in inventory["exclusions"]["development_modules"]
    assert inventory["exclusions"]["model_weights"] == "on-demand managed cache only"
    assert inventory["acceleration"] == {
        "baseline": "cpu",
        "optional": ["coreml", "metal", "mlx"],
    }
    assert inventory["runtime_size_bytes"] == len(b"frozenffmpegffprobebackup-age")


@pytest.mark.parametrize("target", ["windows-x64", "linux-x86_64"])
def test_non_macos_inventory_declares_cpu_fallback_and_optional_cuda(
    tmp_path, monkeypatch, target
):
    release = _load_build_release()
    runtime = tmp_path / "Editarr"
    helpers = runtime / "_internal" / "helpers"
    helpers.mkdir(parents=True)
    (runtime / "Editarr.exe" if target == "windows-x64" else runtime / "Editarr").write_bytes(
        b"frozen"
    )
    for name in ("ffmpeg", "ffprobe", "backup-age"):
        suffix = ".exe" if target == "windows-x64" else ""
        (helpers / f"{name}{suffix}").write_bytes(name.encode())
    monkeypatch.setattr(release, "_git_revision", lambda: "a" * 40)

    inventory = release._inventory(
        runtime=runtime,
        target=target,
        frontend_hashes={},
        packages={"CTranslate2": "4.8.1"},
        helpers={
            "ffmpeg": tmp_path / "ffmpeg",
            "ffprobe": tmp_path / "ffprobe",
            "backup-age": tmp_path / "backup-age",
        },
    )

    assert inventory["acceleration"] == {
        "baseline": "cpu",
        "optional": ["cuda"],
        "packaged_backends": ["ctranslate2"],
        "gpu_driver": "host-provided",
    }


def test_windows_runtime_layout_uses_exe_entrypoint_and_helpers(tmp_path):
    from native_build.runtime_layout import helper_name, resolve_executable

    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    executable = runtime / "Editarr.exe"
    executable.write_bytes(b"windows frozen executable")
    executable.chmod(0o755)

    assert resolve_executable(runtime, "windows-x64") == executable
    assert helper_name("ffmpeg", "windows-x64") == "ffmpeg.exe"
    assert helper_name("ffprobe", "windows-x64") == "ffprobe.exe"


def test_runtime_layout_rejects_missing_wrong_or_ambiguous_target_entrypoints(tmp_path):
    from native_build.runtime_layout import resolve_executable

    runtime = tmp_path / "Editarr"
    runtime.mkdir()
    posix = runtime / "Editarr"
    posix.write_bytes(b"posix frozen executable")
    posix.chmod(0o755)

    with pytest.raises(RuntimeError, match="wrong frozen entrypoint"):
        resolve_executable(runtime, "windows-x64")
    windows = runtime / "Editarr.exe"
    windows.write_bytes(b"windows frozen executable")
    windows.chmod(0o755)
    with pytest.raises(RuntimeError, match="ambiguous frozen Editarr entrypoints"):
        resolve_executable(runtime, "windows-x64")
    posix.unlink()
    assert resolve_executable(runtime, "windows-x64") == windows


def test_runtime_layout_keeps_posix_and_macos_app_contracts(tmp_path):
    from native_build.runtime_layout import resolve_executable

    raw = tmp_path / "linux-runtime"
    raw.mkdir()
    posix = raw / "Editarr"
    posix.write_bytes(b"posix frozen executable")
    posix.chmod(0o755)
    assert resolve_executable(raw, "linux-x86_64") == posix

    app = tmp_path / "Editarr.app"
    launcher = app / "Contents" / "MacOS" / "Editarr"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"macos app launcher")
    launcher.chmod(0o755)
    assert resolve_executable(app, "macos-arm64") == launcher
    with pytest.raises(RuntimeError, match="cannot use a macOS .app layout"):
        resolve_executable(app, "windows-x64")


def test_native_runtime_requires_collected_staged_frontend(tmp_path):
    release = _load_build_release()
    runtime = tmp_path / "Editarr"
    frontend = runtime / "_internal" / "scanner" / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("native UI", encoding="utf-8")

    release._assert_collected_frontend(
        runtime, {"index.html": release._sha256(frontend / "index.html")}
    )

    (frontend / "index.html").unlink()
    with pytest.raises(RuntimeError, match="do not match"):
        release._assert_collected_frontend(runtime, {"index.html": "x"})


def test_wheel_requires_packaged_backup_passphrase_helper(tmp_path):
    release = _load_build_release()
    wheel = tmp_path / "editarr-0.1.0-py3-none-any.whl"
    with pytest.raises(RuntimeError, match="expected artifact"):
        release._assert_wheel_backup_helper(wheel)
    with zipfile.ZipFile(wheel, "w") as artifact:
        artifact.writestr("scanner/__init__.py", "")
    with pytest.raises(RuntimeError, match="age passphrase helper"):
        release._assert_wheel_backup_helper(wheel)
    with zipfile.ZipFile(wheel, "a") as artifact:
        artifact.writestr("scanner/backup/age_passphrase.exp", "helper")
    release._assert_wheel_backup_helper(wheel)


def test_native_build_rejects_missing_or_symlinked_ffprobe(tmp_path):
    release = _load_build_release()
    with pytest.raises(RuntimeError, match="relocatable ffprobe"):
        release._ffprobe_helper(None)
    target = tmp_path / "target"
    target.write_text("helper", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(RuntimeError, match="regular"):
        release._ffprobe_helper(str(link))


def test_native_build_preserves_preexisting_frontend_staging(tmp_path, monkeypatch):
    release = _load_build_release()
    staging = tmp_path / "frontend"
    staging.mkdir()
    sentinel = staging / "index.html"
    sentinel.write_text("another build owns this", encoding="utf-8")
    monkeypatch.setattr(release, "STAGED_FRONTEND", staging)
    monkeypatch.setattr(release, "_require_command", lambda *args: None)
    monkeypatch.setattr(release, "_target_inputs", lambda: ("macos-arm64", {}))
    monkeypatch.setattr(release, "_imageio_ffmpeg", lambda: tmp_path / "ffmpeg")
    monkeypatch.setattr(release, "_ffprobe_helper", lambda *args: tmp_path / "ffprobe")
    monkeypatch.setattr(release, "_build_backup_age", lambda *args: tmp_path / "backup-age")
    monkeypatch.setattr(release, "_stage_helpers", lambda helpers, *args, **kwargs: helpers)
    args = SimpleNamespace(
        target=None,
        macos_release=False,
        source_media_release=False,
        ffmpeg_helper=None,
        ffprobe_helper=None,
        out_dir=tmp_path / "output",
        work_dir=tmp_path / "work",
        test_fake_models=False,
    )

    with pytest.raises(RuntimeError, match="release staging already exists"):
        release.build_native(args)

    assert sentinel.read_text(encoding="utf-8") == "another build owns this"
    assert not list(args.work_dir.iterdir())


def test_windows_batch_tool_shims_use_the_command_processor(monkeypatch):
    release = _load_build_release()
    monkeypatch.setattr(release.shutil, "which", lambda _command: r"C:\tools\pnpm.CMD")

    assert release._requires_windows_shell(["pnpm", "install"], platform_name="nt")
    assert not release._requires_windows_shell(["pnpm", "install"], platform_name="posix")

    monkeypatch.setattr(release.shutil, "which", lambda _command: r"C:\tools\uv.exe")
    assert not release._requires_windows_shell(["uv", "run"], platform_name="nt")


def test_helper_staging_normalizes_runtime_filenames(tmp_path):
    release = _load_build_release()
    source = tmp_path / "ffmpeg-macos-aarch64-v7.1"
    source.write_text("helper", encoding="utf-8")
    source.chmod(0o755)

    staged = release._stage_helpers(
        {"ffmpeg": source}, tmp_path / "work", target="macos-arm64"
    )

    assert staged["ffmpeg"].name == "ffmpeg"
    assert staged["ffmpeg"].read_text(encoding="utf-8") == "helper"
    if os.name == "posix":
        assert staged["ffmpeg"].stat().st_mode & 0o111


def test_windows_helper_staging_preserves_exe_extension(tmp_path):
    release = _load_build_release()
    source = tmp_path / "ffmpeg-windows-x64.exe"
    source.write_text("helper", encoding="utf-8")
    source.chmod(0o755)

    staged = release._stage_helpers(
        {"ffmpeg": source}, tmp_path / "work", target="windows-x64"
    )

    assert staged["ffmpeg"].name == "ffmpeg.exe"
    assert staged["ffmpeg"].read_text(encoding="utf-8") == "helper"


def test_windows_inventory_records_exe_helper_paths(tmp_path, monkeypatch):
    release = _load_build_release()
    runtime = tmp_path / "Editarr"
    helpers = runtime / "_internal" / "helpers"
    helpers.mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "backup-age.exe"):
        (helpers / name).write_bytes(name.encode())
    monkeypatch.setattr(release, "_git_revision", lambda: "a" * 40)

    inventory = release._inventory(
        runtime=runtime,
        target="windows-x64",
        frontend_hashes={},
        packages={},
        helpers={
            "ffmpeg": tmp_path / "ffmpeg.exe",
            "ffprobe": tmp_path / "ffprobe.exe",
            "backup-age": tmp_path / "backup-age.exe",
        },
    )

    assert inventory["helpers"]["ffmpeg"]["path"] == "_internal/helpers/ffmpeg.exe"
    assert inventory["helpers"]["ffprobe"]["path"] == "_internal/helpers/ffprobe.exe"
    assert inventory["helpers"]["backup-age"]["path"] == "_internal/helpers/backup-age.exe"


def test_windows_frozen_smoke_keeps_only_required_system_environment(tmp_path, monkeypatch):
    release = _load_build_release()
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setenv("PATH", r"C:\Python311;C:\Program Files\nodejs")

    environment = release._frozen_smoke_environment(tmp_path, "windows-x64")

    assert environment["PATH"] == r"C:\Windows\System32"
    assert environment["SystemRoot"] == r"C:\Windows"
    assert environment["COMSPEC"] == r"C:\Windows\System32\cmd.exe"
    assert environment["USERPROFILE"] == str(tmp_path / "home")
    assert environment["LOCALAPPDATA"] == str(tmp_path / "local-app-data")
    assert environment["TEMP"] == str(tmp_path / "tmp")
    assert "Python311" not in environment["PATH"]
    assert "nodejs" not in environment["PATH"]


def test_posix_frozen_smoke_keeps_system_only_path(tmp_path):
    release = _load_build_release()

    environment = release._frozen_smoke_environment(tmp_path, "linux-x86_64")

    assert environment["PATH"] == "/usr/bin:/bin"
    assert "SystemRoot" not in environment


def test_test_fake_models_are_opt_in_build_inputs():
    # The marker is deliberately not collected by the normal spec path; this
    # check keeps the release wrapper's explicit test-only switch discoverable.
    assert "EDITARR_PYINSTALLER_TEST_FAKE_MODELS" in Path("native_build/editarr.spec").read_text(
        encoding="utf-8"
    )
    assert "--test-fake-models" in Path("scripts/build_release.py").read_text(encoding="utf-8")
    assert (Path("native_build") / "native-test-fake-models.marker").is_file()
    helper = Path("scanner/testing/test_fake_models.py").read_text(encoding="utf-8")
    assert "EDITARR_TEST_FAKE_MODELS" in helper
    assert "is_frozen_application" in helper


def test_fake_model_gate_fails_closed_without_the_marked_frozen_runtime(tmp_path, monkeypatch):
    from scanner.testing import test_fake_models

    marker_root = tmp_path / "resources"
    monkeypatch.setenv("EDITARR_TEST_FAKE_MODELS", "1")
    monkeypatch.setattr(test_fake_models, "is_frozen_application", lambda: True)
    monkeypatch.setattr(
        test_fake_models,
        "get_runtime_paths",
        lambda: SimpleNamespace(resources_dir=marker_root),
    )

    assert not test_fake_models.enabled()
    (marker_root / test_fake_models.MARKER).parent.mkdir()
    (marker_root / test_fake_models.MARKER).write_text("test fixture only\n", encoding="utf-8")
    assert test_fake_models.enabled()

    monkeypatch.setattr(test_fake_models, "is_frozen_application", lambda: False)
    assert not test_fake_models.enabled()


def test_runtime_audit_rejects_managed_data_capture(tmp_path):
    release = _load_build_release()
    runtime = tmp_path / "Editarr"
    (runtime / "data").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="captured managed"):
        release._assert_clean_runtime(runtime)


def test_runtime_pruning_removes_third_party_tests_and_corpora(tmp_path):
    release = _load_build_release()
    runtime = tmp_path / "Editarr"
    test_file = runtime / "_internal" / "sklearn" / "tests" / "fixture.txt"
    corpus_file = runtime / "_internal" / "package" / "corpora" / "fixture.txt"
    test_file.parent.mkdir(parents=True)
    corpus_file.parent.mkdir(parents=True)
    test_file.write_text("test", encoding="utf-8")
    corpus_file.write_text("corpus", encoding="utf-8")
    coverage_file = runtime / "_internal" / "coverage" / "__init__.py"
    pytest_metadata = runtime / "_internal" / "pytest_asyncio-1.4.0.dist-info" / "METADATA"
    coverage_file.parent.mkdir(parents=True)
    pytest_metadata.parent.mkdir(parents=True)
    coverage_file.write_text("coverage", encoding="utf-8")
    pytest_metadata.write_text("pytest", encoding="utf-8")

    release._prune_excluded_runtime_content(runtime)

    assert not test_file.exists()
    assert not corpus_file.exists()
    assert not coverage_file.exists()
    assert not pytest_metadata.exists()
