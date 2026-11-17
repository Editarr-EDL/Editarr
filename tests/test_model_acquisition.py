import hashlib
import json
import multiprocessing
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner import model_acquisition
from scanner.model_acquisition import (
    AcquisitionCancelled,
    AcquisitionError,
    ArtifactFile,
    ArtifactLicense,
    ModelAcquisitionService,
    ModelArtifact,
    ModelManifest,
    artifact_ids_for_backend,
    manifest,
    scoped_acquisition_cancellation,
)
from scanner.modeling import model_acquisition_manifest, model_acquisition_service

_PAYLOADS = {"config.json": b'{"model":"fake"}', "weights.bin": b"fake-weights" * 128}


def _git_blob_sha1(value: bytes) -> str:
    return hashlib.sha1(f"blob {len(value)}\0".encode() + value).hexdigest()


def _fake_manifest() -> ModelManifest:
    artifact = ModelArtifact(
        id="fake-model",
        backend_ids=("fake_backend",),
        backend_version="test-1",
        distribution="huggingface",
        repository="example/fake-model",
        revision="a" * 40,
        required_patterns=("config.json", "weights.bin"),
        source_url="https://example.invalid/fake-model",
        license=ArtifactLicense(
            spdx="MIT",
            status="declared",
            url="https://example.invalid/license",
        ),
        files=(
            ArtifactFile(
                "config.json",
                len(_PAYLOADS["config.json"]),
                git_blob_sha1=_git_blob_sha1(_PAYLOADS["config.json"]),
            ),
            ArtifactFile(
                "weights.bin",
                len(_PAYLOADS["weights.bin"]),
                sha256=hashlib.sha256(_PAYLOADS["weights.bin"]).hexdigest(),
            ),
        ),
    )
    return ModelManifest(schema_version=1, manifest_version="test", artifacts=(artifact,))


class FakeRepository:
    def __init__(self, *, fail: bool = False, delay: float = 0):
        self.calls: list[str] = []
        self.fail = fail
        self.delay = delay

    def fetch(self, artifact, artifact_file, destination, *, progress, cancelled):
        del artifact
        self.calls.append(artifact_file.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = _PAYLOADS[artifact_file.path]
        midpoint = max(1, len(payload) // 2)
        with destination.open("wb") as handle:
            for chunk in (payload[:midpoint], payload[midpoint:]):
                if cancelled():
                    raise AcquisitionCancelled("fake-model")
                handle.write(chunk)
                progress(len(chunk))
                if self.delay:
                    time.sleep(self.delay)
                if self.fail:
                    raise RuntimeError("https://user:secret@example.invalid/?token=private")


class ProcessRepository(FakeRepository):
    def __init__(self, call_log: Path):
        super().__init__(delay=0.05)
        self.call_log = call_log

    def fetch(self, artifact, artifact_file, destination, *, progress, cancelled):
        with self.call_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{artifact_file.path}\n")
        super().fetch(
            artifact,
            artifact_file,
            destination,
            progress=progress,
            cancelled=cancelled,
        )


def _process_acquire(cache_root: str, call_log: str, output) -> None:
    service = ModelAcquisitionService(
        Path(cache_root),
        repository=ProcessRepository(Path(call_log)),
        disk_free=lambda _path: 10**9,
        model_manifest=_fake_manifest(),
        minimum_free_after_download=0,
    )
    output.put(str(service.acquire("fake-model")))


def _service(tmp_path: Path, repository=None, *, disk_free=10**9):
    return ModelAcquisitionService(
        tmp_path / "models",
        repository=repository or FakeRepository(),
        disk_free=lambda _path: disk_free,
        model_manifest=_fake_manifest(),
        minimum_free_after_download=0,
    )


def test_bundled_manifest_is_exhaustive_pinned_and_records_licenses():
    declared = manifest()
    downloaded = [item for item in declared.artifacts if item.distribution == "huggingface"]

    assert declared.schema_version == 1
    assert {
        "faster-whisper-base",
        "mlx-whisper-base",
        "wd-vit-tagger-v3",
        "wd-convnext-tagger-v3",
        "wd-swinv2-tagger-v3",
        "wd-vit-large-tagger-v3",
        "wd-eva02-large-tagger-v3",
        "stable-diffusion-safety-checker",
        "image-safety-classifier-s",
        "siglip2-base-patch16-256",
        "siglip2-moderation-head-nsfw",
        "siglip2-moderation-head-violence",
        "qwen3-vl-4b-instruct-4bit",
        "qwen3-vl-8b-instruct-4bit",
    } == {item.id for item in downloaded}
    assert all(item.revision and len(item.revision) == 40 for item in downloaded)
    assert all(item.files and item.expected_size > 0 for item in downloaded)
    assert all(item.license.url and item.source_url for item in declared.artifacts)
    assert {
        item.id for item in declared.artifacts if item.license.status == "undeclared"
    } == {
        "mlx-whisper-base",
        "stable-diffusion-safety-checker",
    }
    assert declared.artifact("imageio-ffmpeg-python").license.spdx == "BSD-2-Clause"
    assert declared.artifact("ffmpeg-package-binary").license.spdx is None
    qwen_4b = declared.artifact("qwen3-vl-4b-instruct-4bit")
    assert {
        item.path: item.git_blob_sha1 for item in qwen_4b.files if item.git_blob_sha1
    }["vocab.json"] == "4783fe10ac3adce15ac8f358ef5462739852c569"
    profanity_artifacts = {
        "better-profanity-package-data",
        "glin-profanity-package-data",
        "alt-profanity-check-package-data",
    }
    assert {
        item.id: (item.backend_ids, item.package, item.license.spdx)
        for item in declared.artifacts
        if item.id in profanity_artifacts
    } == {
        "better-profanity-package-data": (("better_profanity",), "better-profanity", "MIT"),
        "glin-profanity-package-data": (("glin_profanity",), "glin-profanity", "MIT"),
        "alt-profanity-check-package-data": (
            ("alt_profanity_check",),
            "alt-profanity-check",
            "MIT",
        ),
    }
    assert artifact_ids_for_backend("image_safety_gate", wd_variant="vit") == (
        "wd-vit-tagger-v3",
        "stable-diffusion-safety-checker",
        "image-safety-classifier-s",
    )
    assert artifact_ids_for_backend("qwen") == artifact_ids_for_backend("qwen3_vl")


def test_macos_release_offers_models_with_undeclared_licenses(tmp_path, monkeypatch):
    monkeypatch.setattr(model_acquisition_service, "is_macos_release", lambda: True)
    service = ModelAcquisitionService(tmp_path / "models")
    listed = {entry.artifact_id: entry for entry in service.list_artifacts()}
    assert "ffmpeg-package-binary" not in listed
    for artifact_id in ("mlx-whisper-base", "stable-diffusion-safety-checker"):
        assert listed[artifact_id].license_status == "undeclared"
        assert listed[artifact_id].license_spdx is None
        with pytest.raises(AcquisitionError) as error:
            service.acquire(artifact_id, offline=True)
        assert error.value.code == "offline_missing"


def test_scoped_acquisition_cancellation_stops_lazy_download_without_leaking(tmp_path):
    repository = FakeRepository()
    service = _service(tmp_path, repository)

    with scoped_acquisition_cancellation(lambda: True), pytest.raises(AcquisitionCancelled):
        service.acquire("fake-model")

    assert repository.calls == []
    assert service.acquire("fake-model").is_dir()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.update({"artifacts": "not-a-list"}), "contains no artifacts"),
        (
            lambda raw: raw["artifacts"][0]["files"][0].update({"path": "../outside"}),
            "Unsafe model manifest path",
        ),
        (
            lambda raw: raw["artifacts"][0]["license"].update({"spdx": None}),
            "Declared license has no SPDX",
        ),
        (
            lambda raw: raw["artifacts"][0].update({"source_url": "http://example.invalid"}),
            "Invalid source URL",
        ),
    ],
)
def test_manifest_validation_rejects_malformed_schema_and_metadata(monkeypatch, mutate, message):
    raw = json.loads(Path("scanner/model-manifest-v1.json").read_text(encoding="utf-8"))
    mutate(raw)
    resource = SimpleNamespace(read_text=lambda **_kwargs: json.dumps(raw))
    monkeypatch.setattr(
        model_acquisition_manifest,
        "files",
        lambda _package: SimpleNamespace(joinpath=lambda _name: resource),
    )

    with pytest.raises(ValueError, match=message):
        model_acquisition.load_manifest()


def test_acquire_verifies_atomically_then_reuses_offline(tmp_path):
    repository = FakeRepository()
    service = _service(tmp_path, repository)
    progress = []

    installed = service.acquire(
        "fake-model", progress=lambda done, total: progress.append((done, total))
    )

    assert (installed / ".complete.json").is_file()
    assert service.status("fake-model").state == "ready"
    assert progress[-1][0] == progress[-1][1]
    assert service.acquire("fake-model", offline=True) == installed
    assert repository.calls == ["config.json", "weights.bin"]
    assert not list((service.cache_root / ".staging").glob("*.partial-*"))


def test_corruption_cannot_appear_ready_and_offline_reuse_is_rejected(tmp_path):
    service = _service(tmp_path)
    installed = service.acquire("fake-model")
    (installed / "weights.bin").write_bytes(b"X" * len(_PAYLOADS["weights.bin"]))

    assert service.status("fake-model").state == "not_downloaded"
    with pytest.raises(AcquisitionError, match="Connect to the network") as exc_info:
        service.acquire("fake-model", offline=True)
    assert exc_info.value.code == "offline_missing"


def test_interrupted_download_is_removed_and_error_hides_secrets(tmp_path):
    service = _service(tmp_path, FakeRepository(fail=True))

    with pytest.raises(AcquisitionError) as exc_info:
        service.acquire("fake-model")

    assert exc_info.value.code == "download_failed"
    assert "secret" not in str(exc_info.value)
    status = service.status("fake-model")
    assert status.state == "failed"
    assert "secret" not in (status.error_detail or "")
    assert not list((service.cache_root / ".staging").glob("*.partial-*"))


def test_download_is_cancellable_and_a_later_retry_clears_the_request(tmp_path):
    service = _service(tmp_path, FakeRepository(delay=0.01))
    progress = []

    with pytest.raises(AcquisitionCancelled):
        service.acquire(
            "fake-model",
            progress=lambda done, _total: progress.append(done),
            cancelled=lambda: bool(progress),
        )

    assert service.status("fake-model").state == "cancelled"
    service.repository = FakeRepository()
    assert service.acquire("fake-model").is_dir()
    assert service.status("fake-model").state == "ready"


def test_disk_preflight_fails_before_repository_access(tmp_path):
    repository = FakeRepository()
    service = _service(tmp_path, repository, disk_free=1)

    with pytest.raises(AcquisitionError) as exc_info:
        service.acquire("fake-model")

    assert exc_info.value.code == "insufficient_space"
    assert repository.calls == []


def test_concurrent_threads_share_one_verified_acquisition(tmp_path):
    repository = FakeRepository(delay=0.02)
    service = _service(tmp_path, repository)
    results = []

    threads = [
        threading.Thread(target=lambda: results.append(service.acquire("fake-model")))
        for _index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert results[0] == results[1]
    assert repository.calls == ["config.json", "weights.bin"]


def test_concurrent_processes_share_one_verified_acquisition(tmp_path):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    call_log = tmp_path / "calls.txt"
    cache_root = tmp_path / "models"
    processes = [
        context.Process(target=_process_acquire, args=(str(cache_root), str(call_log), output))
        for _index in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert [process.exitcode for process in processes] == [0, 0]
    assert output.get(timeout=1) == output.get(timeout=1)
    assert call_log.read_text(encoding="utf-8").splitlines() == [
        "config.json",
        "weights.bin",
    ]


def test_removal_is_confined_to_one_managed_artifact(tmp_path):
    service = _service(tmp_path)
    installed = service.acquire("fake-model")
    durable = tmp_path / "durable" / "scanner.db"
    durable.parent.mkdir()
    durable.write_text("keep", encoding="utf-8")
    sibling = service.artifact_root / "other-model" / "keep.bin"
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(b"keep")

    assert service.remove("fake-model") is True
    assert not installed.exists()
    assert durable.read_text(encoding="utf-8") == "keep"
    assert sibling.read_bytes() == b"keep"
    assert service.remove("fake-model") is False


def test_removal_rejects_an_artifact_root_redirected_outside_the_cache(tmp_path):
    service = _service(tmp_path)
    outside = tmp_path / "durable"
    outside.mkdir()
    (outside / "keep").write_bytes(b"keep")
    service.cache_root.mkdir(parents=True)
    service.artifact_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AcquisitionError) as exc_info:
        service.remove("fake-model")

    assert exc_info.value.code == "unsafe_path"
    assert (outside / "keep").read_bytes() == b"keep"


def test_acquire_rejects_an_artifact_destination_redirected_outside_cache(tmp_path):
    service = _service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    service.artifact_root.mkdir(parents=True)
    (service.artifact_root / "fake-model").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AcquisitionError) as exc_info:
        service.acquire("fake-model")

    assert exc_info.value.code == "unsafe_path"
    assert not list(outside.iterdir())


def test_acquire_and_cancel_reject_redirected_control_files(tmp_path):
    service = _service(tmp_path)
    outside = tmp_path / "outside-control"
    outside.write_bytes(b"keep")
    (service.cache_root / ".cancel").mkdir(parents=True)
    service._cancel_path("fake-model").symlink_to(outside)

    with pytest.raises(AcquisitionError) as exc_info:
        service.request_cancel("fake-model")
    assert exc_info.value.code == "unsafe_path"
    assert outside.read_bytes() == b"keep"


def test_status_ignores_state_from_an_old_manifest_revision(tmp_path):
    service = _service(tmp_path)
    state = service._state_path("fake-model")
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "artifact_id": "fake-model",
                "manifest_version": "old",
                "revision": "b" * 40,
                "state": "ready",
                "bytes_downloaded": 999,
            }
        ),
        encoding="utf-8",
    )

    status = service.status("fake-model")

    assert status.state == "not_downloaded"
    assert status.bytes_downloaded is None


def test_status_reports_an_unlocked_in_progress_state_as_interrupted(tmp_path):
    service = _service(tmp_path)
    artifact = service.manifest.artifact("fake-model")
    service._write_state(artifact, "downloading", bytes_downloaded=123)
    lock = service._lock_path("fake-model")
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"\0")

    status = service.status("fake-model")

    assert status.state == "failed"
    assert status.bytes_downloaded == 123
    assert status.error_code == "interrupted"
    assert "retry" in (status.error_detail or "").lower()


def test_status_is_side_effect_free_for_an_empty_cache(tmp_path):
    service = _service(tmp_path)

    assert service.status("fake-model").state == "not_downloaded"
    assert not service.cache_root.exists()
