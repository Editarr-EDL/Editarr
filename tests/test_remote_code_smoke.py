"""Frozen-runtime checks for pinned remote model code and the opt-in model probe."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.models import McfCategoryFamily
from scanner.pipeline import d1_detector
from scanner.pipeline.decision_detection import FrameDecision
from scanner.runtime import model_smoke
from scanner.runtime.remote_code_smoke import remote_code_imports, smoke_remote_code

ROOT = Path(__file__).resolve().parents[1]
CACHED_D1 = (
    Path.home() / "Library" / "Caches" / "Editarr" / "models" / "artifacts" / "d1-3b"
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


def _status(state):
    return lambda: SimpleNamespace(status=lambda _artifact: SimpleNamespace(state=state))


def test_model_probe_never_acquires_a_missing_artifact(tmp_path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    monkeypatch.setattr(
        "scanner.model_acquisition.get_acquisition_service", _status("not_downloaded")
    )
    monkeypatch.setattr(d1_detector, "score_frame", pytest.fail)

    with pytest.raises(RuntimeError, match="never downloads"):
        model_smoke.run_model_probe("d1-3b", frame)
    with pytest.raises(RuntimeError, match="Unsupported"):
        model_smoke.run_model_probe("nudenet", frame)


def test_model_probe_reports_decisions_and_releases_the_model(tmp_path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    released = []
    decision = FrameDecision(
        scores={McfCategoryFamily.VIOLENCE: 0.9, McfCategoryFamily.NUDITY: 0.1},
        thresholds=d1_detector.THRESHOLDS,
        levels={McfCategoryFamily.VIOLENCE: 2},
    )
    monkeypatch.setattr("scanner.model_acquisition.get_acquisition_service", _status("ready"))
    monkeypatch.setattr(d1_detector, "score_frame", lambda path: decision)
    monkeypatch.setattr(d1_detector, "release", lambda: released.append(True))

    result = model_smoke.run_model_probe("d1-3b", frame)

    assert result["positives"] == ["violence"]
    assert result["severity_levels"] == {"violence": 2}
    assert result["revision"] == d1_detector.MODEL_REVISION
    assert released == [True]
