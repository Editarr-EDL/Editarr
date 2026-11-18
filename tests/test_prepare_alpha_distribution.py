"""Check bootstrap integrity and the contract between platform jobs and the publisher."""

import io
import json
import tarfile
from pathlib import Path

import pytest

from scripts import prepare_alpha_distribution as distribution


def test_bootstrap_extracts_selected_inputs_and_rejects_corruption(tmp_path, monkeypatch):
    kind = "Third-Party-Source"
    archive = tmp_path / f"Editarr-v0.1.2-macOS-{kind}.tar.gz"
    with tarfile.open(archive, "w:gz") as contents:
        for name in ("media", "mac-only"):
            entry = tarfile.TarInfo(name)
            entry.size = len(name)
            contents.addfile(entry, io.BytesIO(name.encode()))
    monkeypatch.setenv("EDITARR_RELEASE_BOOTSTRAP_DIR", str(tmp_path))
    monkeypatch.setitem(distribution.BOOTSTRAP_HASHES, kind, distribution.sha256(archive))
    output = tmp_path / "output"
    output.mkdir()
    distribution.extract_bootstrap(kind, output, {"media"})
    assert (output / "media").read_text() == "media"
    assert not (output / "mac-only").exists()
    archive.write_bytes(b"unexpected bytes")
    with pytest.raises(ValueError, match="Unexpected SHA-256 digest"):
        distribution.extract_bootstrap(kind, output, {"media"})


@pytest.mark.parametrize(
    ("target", "filename"),
    [
        ("windows-x64", "Editarr-v0.1.5-Windows-x64-Setup.exe"),
        ("linux-x86_64", "Editarr-v0.1.5-Linux-x86_64.AppImage"),
    ],
)
def test_packaged_artifacts_record_the_verified_revision(tmp_path, monkeypatch, target, filename):
    revision = "a" * 40
    monkeypatch.setattr(distribution, "validate_release_versions", lambda root: "0.1.5")
    monkeypatch.setattr(distribution, "source_identity", lambda root: (revision, False))
    monkeypatch.setattr(distribution, "collect_notices", lambda bootstrap: None)
    bootstrap = tmp_path / "bootstrap"
    for directory in ("notices", "sources"):
        path = bootstrap / directory
        path.mkdir(parents=True)
        (path / "media.txt").write_text(directory)
    installer = tmp_path / "build-output"
    installer.write_bytes(b"platform installer")
    output = tmp_path / "release-assets"
    distribution.package(Path.cwd(), target, bootstrap, installer, output)
    assert (output / filename).read_bytes() == installer.read_bytes()
    evidence = json.loads((output / "artifact-verification.json").read_text())
    assert (evidence["version"], evidence["source_revision"], evidence["target"]) == (
        "0.1.5", revision, target,
    )
    assert len(evidence["sha256"]) == 3
    assert evidence["sha256"] == {
        file.name: distribution.sha256(file)
        for file in output.iterdir()
        if file.suffix != ".json"
    }
