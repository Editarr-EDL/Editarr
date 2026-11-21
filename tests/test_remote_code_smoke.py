"""Frozen-runtime checks for pinned remote model code and the opt-in model probe."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import socket
from pathlib import Path

import pytest

from scanner.evaluation.severity_rubric import RUBRIC
from scanner.model_acquisition import ArtifactFile, ModelAcquisitionService, ModelManifest, manifest
from scanner.pipeline import d1_detector
from scanner.pipeline.decision_detection import DETECTED_FAMILIES
from scanner.runtime import model_smoke
from scanner.runtime.remote_code_smoke import remote_code_imports, smoke_remote_code

ROOT = Path(__file__).resolve().parents[1]
CACHED_D1 = (
    Path.home()
    / "Library"
    / "Caches"
    / "Editarr"
    / "models"
    / "artifacts"
    / "d1-3b"
    / d1_detector.MODEL_REVISION
)


@pytest.fixture
def modules_cache(tmp_path, monkeypatch):
    from transformers import dynamic_module_utils

    cache = tmp_path / "modules"
    monkeypatch.setenv("HF_MODULES_CACHE", str(cache))
    monkeypatch.setattr(dynamic_module_utils, "HF_MODULES_CACHE", str(cache))
    return cache


def test_remote_code_imports_keep_third_party_absolute_imports(tmp_path):
    (tmp_path / "a.py").write_text(
        "from __future__ import annotations\nimport json\nimport torch.nn as nn\n"
        "from transformers import B, A\nfrom .sibling import local\n"
    )
    (tmp_path / "b.py").write_text(
        "def lazy():\n    from transformers import C\n    from PIL import Image\n"
    )

    assert remote_code_imports([tmp_path / "a.py", tmp_path / "b.py"]) == {
        "PIL": ("Image",),
        "torch.nn": (),
        "transformers": ("A", "B", "C"),
    }


@pytest.mark.skipif(not CACHED_D1.is_dir(), reason="the pinned d1-3B artifact is not cached")
def test_pinned_d1_imports_match_the_cached_artifact():
    manifest = json.loads((ROOT / "scanner" / "model-manifest-v1.json").read_text())
    artifact = next(item for item in manifest["artifacts"] if item["id"] == "d1-3b")
    sources = sorted(CACHED_D1.glob("*.py"))
    blobs = {item["path"]: item.get("git_blob_sha1") for item in artifact["files"]}
    for source in sources:
        content = source.read_bytes()
        digest = hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()
        assert digest == blobs[source.name]

    assert remote_code_imports(sources) == d1_detector.REMOTE_CODE_IMPORTS


def test_remote_code_smoke_loads_pinned_imports_and_cleans_up(modules_cache):
    smoke_remote_code(d1_detector.REMOTE_CODE_IMPORTS, d1_detector.RESOLVED_MODULES)

    assert not list((modules_cache / "transformers_modules").glob("editarr_remote_probe*"))


@pytest.mark.parametrize(
    ("imports", "resolved"),
    [({"editarr_absent_package": ()}, ()), ({}, ("transformers.models.editarr_absent",))],
)
def test_remote_code_smoke_names_a_missing_module(modules_cache, imports, resolved):
    with pytest.raises(RuntimeError, match="editarr_absent"):
        smoke_remote_code(imports, resolved)


def test_remote_code_smoke_rejects_an_ignored_modules_cache(modules_cache, monkeypatch):
    monkeypatch.setenv("HF_MODULES_CACHE", str(modules_cache.parent / "elsewhere"))

    with pytest.raises(RuntimeError, match="ignores the configured model cache"):
        smoke_remote_code({}, ())


class _DownloadAttempted(AssertionError):
    """Raised by every network or acquisition entry point the probe must never reach."""


def _refuse(*_args, **_kwargs):
    raise _DownloadAttempted("the probe tried to acquire or download")


class _RefusingRepository:
    def __getattr__(self, _name):
        return _refuse


@pytest.fixture
def d1_cache(tmp_path, monkeypatch):
    """A fake d1-3B manifest entry and cache, with every download path refused."""
    import huggingface_hub

    from scanner.modeling import model_acquisition_transfer

    real = manifest().artifact("d1-3b")
    config = b"{}"
    artifact = dataclasses.replace(
        real,
        files=(
            ArtifactFile("config.json", len(config), sha256=hashlib.sha256(config).hexdigest()),
        ),
    )
    service = ModelAcquisitionService(
        tmp_path / "models",
        repository=_RefusingRepository(),
        model_manifest=ModelManifest(1, manifest().manifest_version, (artifact,)),
    )
    for target, name in (
        (model_acquisition_transfer, "acquire"),
        (ModelAcquisitionService, "acquire"),
        (d1_detector, "acquire_artifact"),
        (huggingface_hub, "hf_hub_download"),
        (huggingface_hub, "snapshot_download"),
        (huggingface_hub.file_download, "hf_hub_download"),
        (socket.socket, "connect"),
    ):
        monkeypatch.setattr(target, name, _refuse)
    monkeypatch.setattr("scanner.model_acquisition.get_acquisition_service", lambda: service)
    monkeypatch.setattr(d1_detector, "_CACHE", {})
    monkeypatch.setattr(d1_detector, "require_available", lambda _backend: None)
    monkeypatch.setattr(d1_detector, "d1_transformers_supported", lambda: True)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv(name, raising=False)
    directory = service.artifact_root / "d1-3b" / real.revision

    def install():
        directory.mkdir(parents=True)
        (directory / "config.json").write_bytes(config)
        (directory / ".complete.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_version": manifest().manifest_version,
                    "artifact_id": "d1-3b",
                    "revision": real.revision,
                    "files": service._verified_files(artifact, directory),
                }
            )
        )
        return directory

    return install


def _fake_transformers(monkeypatch, on_load=lambda: None):
    """Replace transformers' AutoModel with a stand-in that records how it was loaded."""
    from huggingface_hub import constants

    loads = []
    answers = {
        family.value: {"noul": 0.9 if family.value == "violence" else 0.1}
        for family in DETECTED_FAMILIES
    }
    answers.update(
        {
            f"sev_{family.value}": {
                "probabilities": {str(level): float(level == 1) for level in range(len(ladder))}
            }
            for family, ladder in RUBRIC.items()
        }
    )

    class _Model:
        def to(self, _device):
            return self

        def eval(self):
            return self

        def system_one(self, _state, _questions, images):
            return {"answers": answers}

    def from_pretrained(path, **kwargs):
        loads.append(
            (
                path,
                kwargs,
                constants.HF_HUB_OFFLINE,
                os.environ.get("HF_HUB_OFFLINE"),
                os.environ.get("TRANSFORMERS_OFFLINE"),
            )
        )
        on_load()
        return _Model()

    transformers = type(
        "transformers",
        (),
        {"AutoModel": type("A", (), {"from_pretrained": staticmethod(from_pretrained)})},
    )
    real = d1_detector._require_dependency
    monkeypatch.setattr(
        d1_detector,
        "_require_dependency",
        lambda name, hint: transformers if name == "transformers" else real(name, hint),
    )
    return loads


def _frame(tmp_path):
    from PIL import Image

    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (32, 18), "black").save(frame)
    return frame


def test_model_probe_loads_the_verified_cache_offline_without_acquiring(
    tmp_path, monkeypatch, d1_cache
):
    from huggingface_hub import constants

    directory = d1_cache()
    loads = _fake_transformers(monkeypatch)
    offline_before = constants.HF_HUB_OFFLINE

    result = model_smoke.run_model_probe("d1-3b", _frame(tmp_path))

    assert [(path, kwargs["local_files_only"]) for path, kwargs, *_ in loads] == [
        (str(directory), True)
    ]
    assert [offline for _path, _kwargs, *offline in loads] == [[True, "1", "1"]]
    assert result["positives"] == ["violence"]
    assert result["severity_levels"] == {family.value: 1 for family in RUBRIC}
    assert d1_detector._CACHE == {}
    assert constants.HF_HUB_OFFLINE == offline_before
    assert "HF_HUB_OFFLINE" not in os.environ and "TRANSFORMERS_OFFLINE" not in os.environ


def test_model_probe_fails_fast_on_a_missing_artifact(tmp_path, monkeypatch, d1_cache):
    loads = _fake_transformers(monkeypatch)

    with pytest.raises(RuntimeError, match="never downloads"):
        model_smoke.run_model_probe("d1-3b", _frame(tmp_path))
    assert loads == []
    with pytest.raises(RuntimeError, match="Unsupported"):
        model_smoke.run_model_probe("nudenet", _frame(tmp_path))


def test_model_probe_rejects_an_artifact_that_fails_verification(tmp_path, monkeypatch, d1_cache):
    directory = d1_cache()
    (directory / "config.json").write_bytes(b"[]")
    loads = _fake_transformers(monkeypatch)

    with pytest.raises(RuntimeError, match="never downloads"):
        model_smoke.run_model_probe("d1-3b", _frame(tmp_path))
    assert loads == []


def test_model_probe_fails_when_the_artifact_changes_while_loading(tmp_path, monkeypatch, d1_cache):
    directory = d1_cache()
    config = directory / "config.json"
    _fake_transformers(
        monkeypatch, on_load=lambda: os.utime(config, ns=(1, config.stat().st_mtime_ns + 1))
    )

    with pytest.raises(RuntimeError, match="changed in the model cache"):
        model_smoke.run_model_probe("d1-3b", _frame(tmp_path))
