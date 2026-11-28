"""Verify test-image exclusions with Docker's actual context parser."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_docker_context_excludes_environment_files(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is not installed")
    # The suite runs with a scratch HOME (see conftest); Docker's CLI plugins and
    # contexts live under the real one.
    env = {**os.environ, "HOME": os.environ.get("EDITARR_TESTS_REAL_HOME", os.environ["HOME"])}
    ready = subprocess.run([docker, "info"], capture_output=True, timeout=30, check=False, env=env)
    if ready.returncode:
        pytest.skip("Docker daemon is unavailable")
    buildx = subprocess.run(
        [docker, "buildx", "version"], capture_output=True, timeout=30, check=False, env=env
    )
    if buildx.returncode:
        pytest.skip("Docker buildx is unavailable")
    context = tmp_path / "context"
    context.mkdir()
    shutil.copyfile(Path(".dockerignore"), context / ".dockerignore")
    (context / "Dockerfile").write_text("FROM scratch\nCOPY . /proof/\n", encoding="utf-8")
    environment_files = [
        Path(directory) / name
        for directory in (".", "nested")
        for name in (".env", ".env.local", ".env.test")
    ]
    for relative in environment_files:
        path = context / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SYNTHETIC_SECRET=not-a-credential\n", encoding="utf-8")
    (context / "keep.txt").write_text("retained", encoding="utf-8")
    output = tmp_path / "output"

    built = subprocess.run(
        [docker, "build", "--network=none", "--output", f"type=local,dest={output}", str(context)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )

    assert built.returncode == 0, built.stderr
    assert (output / "proof" / "keep.txt").read_text(encoding="utf-8") == "retained"
    for relative in environment_files:
        assert not (output / "proof" / relative).exists(), relative
