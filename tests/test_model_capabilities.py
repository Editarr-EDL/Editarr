"""Native model capability contract and detector-only enrichment coverage."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.models import FindingCreate, McfCategoryFamily, ModelCapability, Severity
from scanner.pipeline import runner, visual
from scanner.settings import capabilities, model_runtime

_PACKAGES = {
    "faster_whisper",
    "mlx_whisper",
    "better_profanity",
    "glin_profanity",
    "profanity_check",
    "nudenet",
    "onnxruntime",
    "numpy",
    "pandas",
    "torch",
    "transformers",
    "huggingface_hub",
    "PIL",
    "timm",
    "safetensors",
    "mlx_vlm",
    "mlx",
}


def _environment(
    system: str,
    machine: str,
    *,
    packages: set[str] = _PACKAGES,
    providers: frozenset[str] = frozenset({"CPUExecutionProvider"}),
    torch_devices: frozenset[str] = frozenset(),
    ctranslate2_devices: frozenset[str] = frozenset(),
) -> capabilities.CapabilityEnvironment:
    return capabilities.CapabilityEnvironment(
        system=system,
        machine=machine,
        package_available=packages.__contains__,
        providers=providers,
        torch_devices=torch_devices,
        ctranslate2_devices=ctranslate2_devices,
    )


@pytest.mark.parametrize(
    ("system", "machine", "target"),
    [
        ("Darwin", "arm64", "macos-arm64"),
        ("Windows", "AMD64", "windows-x64"),
        ("Linux", "x86_64", "linux-x86_64"),
    ],
)
def test_target_matrix_declares_only_supported_backends(system, machine, target):
    environment = _environment(system, machine)
    declared = {item.id: item for item in capabilities.model_capabilities(environment)}

    assert environment.target_id == target
    detectors = [declared[backend] for backend in capabilities.SUPPORTED_VIDEO_MODELS]
    assert all(detector.availability == "available" for detector in detectors)
    assert declared["whisper"].availability == "available"
    assert all(
        declared[backend].availability == "available"
        for backend in capabilities.SUPPORTED_AUDIO_FILTERS
        if backend != "qwen"
    )
    assert declared["qwen"].availability == declared["qwen3_vl"].availability
    assert declared["qwen"].reason == declared["qwen3_vl"].reason
    assert declared["qwen"].device == declared["qwen3_vl"].device
    assert declared["qwen"].download == declared["qwen3_vl"].download
    assert declared["qwen"].stage == "audio"
    if target == "macos-arm64":
        assert declared["mlx_whisper"].availability == "available"
        assert declared["qwen3_vl"].availability == "available"
        assert declared["qwen3_vl"].device == "mlx"
        assert declared["qwen3_vl"].enrichment_behavior == "qwen_summary"
    else:
        assert declared["mlx_whisper"].availability == "unavailable"
        assert declared["mlx_whisper"].reason == "unsupported_platform"
        assert declared["qwen3_vl"].availability == "unavailable"
        assert declared["qwen3_vl"].reason == "deferred_gap"
        assert declared["qwen3_vl"].enrichment_behavior == "detector_findings_only"
        assert all(detector.device == "cpu" for detector in detectors)


@pytest.mark.parametrize(
    ("system", "machine", "reason"),
    [
        ("Darwin", "x86_64", "unsupported_architecture"),
        ("Windows", "arm64", "unsupported_architecture"),
        ("Linux", "aarch64", "unsupported_architecture"),
        ("FreeBSD", "x86_64", "unsupported_platform"),
    ],
)
def test_wrong_target_is_never_advertised(system, machine, reason):
    declared = capabilities.model_capabilities(_environment(system, machine))

    assert all(item.availability == "unavailable" for item in declared)
    assert {item.reason for item in declared} == {reason}


def test_missing_package_and_provider_are_truthful_without_importing_model_modules():
    packages = _PACKAGES - {"mlx_vlm"}
    environment = _environment("Darwin", "arm64", packages=packages, providers=frozenset())
    declared = {item.id: item for item in capabilities.model_capabilities(environment)}

    assert declared["qwen3_vl"].reason == "missing_package"
    assert declared["qwen"].reason == "missing_package"
    assert declared["nudenet"].reason == "missing_provider"
    assert declared["nudenet"].download.state == "not_required"
    assert (
        capabilities.capability_for(
            "alt-profanity-check",
            environment=_environment("Windows", "AMD64"),
        ).id
        == "alt_profanity_check"
    )


@pytest.mark.parametrize(
    "system,machine,expected", [("Darwin", "arm64", True), ("Linux", "x86_64", False)]
)
def test_audio_filter_choices_offer_qwen_only_where_supported(
    monkeypatch, system, machine, expected
):
    from scanner import api

    environment = _environment(system, machine)
    monkeypatch.setattr("scanner.http.scan_catalog_api.current_environment", lambda: environment)
    result = api.api_capabilities()
    filters = {choice.id: choice.display_name for choice in result["audio_filters"]}

    assert ("qwen" in filters) is expected
    if expected:
        assert filters["qwen"] == "Qwen (multilingual)"
    assert next(iter(filters)) == capabilities.DEFAULT_AUDIO_FILTER == "better_profanity"


def test_alt_profanity_distribution_uses_its_real_import_name():
    environment = _environment("Linux", "x86_64", packages=_PACKAGES - {"profanity_check"})
    declared = {item.id: item for item in capabilities.model_capabilities(environment)}

    assert declared["alt_profanity_check"].reason == "missing_package"


@pytest.mark.parametrize("missing", ["timm", "safetensors"])
def test_image_safety_gate_checks_every_direct_runtime_dependency(missing):
    environment = _environment("Linux", "x86_64", packages=_PACKAGES - {missing})

    capability = capabilities.capability_for("image_safety_gate", environment=environment)

    assert capability.availability == "unavailable"
    assert capability.reason == "missing_package"


def test_mac_torch_models_claim_metal_only_for_a_packaged_mps_backend():
    cpu_environment = _environment("Darwin", "arm64")
    metal_environment = _environment(
        "Darwin",
        "arm64",
        torch_devices=frozenset({"mps"}),
    )

    for backend_id in (
        "image_safety_gate",
        "siglip2_moderation_nsfw",
        "siglip2_moderation_violence",
    ):
        assert capabilities.capability_for(backend_id, environment=cpu_environment).device == "cpu"
        metal_capability = capabilities.capability_for(
            backend_id,
            environment=metal_environment,
        )
        assert metal_capability.device == "metal"


def test_mac_nudenet_claims_coreml_only_from_a_verified_provider_inventory():
    cpu = _environment("Darwin", "arm64")
    coreml = _environment(
        "Darwin",
        "arm64",
        providers=frozenset({"CPUExecutionProvider", "CoreMLExecutionProvider"}),
    )

    assert capabilities.capability_for("nudenet", environment=cpu).device == "cpu"
    assert capabilities.capability_for("nudenet", environment=coreml).device == "coreml"


def test_windows_and_linux_advertise_only_verified_ctranslate2_cuda():
    providers = frozenset(
        {"CPUExecutionProvider", "CoreMLExecutionProvider", "CUDAExecutionProvider"}
    )
    for system, machine in (("Windows", "AMD64"), ("Linux", "x86_64")):
        environment = _environment(
            system,
            machine,
            providers=providers,
            torch_devices=frozenset({"mps", "cuda"}),
            ctranslate2_devices=frozenset({"cuda"}),
        )
        declared = {item.id: item for item in capabilities.model_capabilities(environment)}

        assert declared["whisper"].device == "cuda"
        assert all(
            declared[backend].device == "cpu"
            for backend in capabilities.SUPPORTED_VIDEO_MODELS
        )


def test_ctranslate2_cuda_probe_requires_device_and_compute_runtime(monkeypatch):
    model_runtime._verified_ctranslate2_devices.cache_clear()
    monkeypatch.setattr(model_runtime, "_installed_package", lambda name: name == "ctranslate2")
    monkeypatch.setattr(
        model_runtime,
        "import_module",
        lambda _name: type(
            "CTranslate2",
            (),
            {
                "get_cuda_device_count": staticmethod(lambda: 1),
                "get_supported_compute_types": staticmethod(lambda _device: {"float16"}),
            },
        ),
    )
    try:
        assert model_runtime._verified_ctranslate2_devices() == frozenset({"cuda"})
    finally:
        model_runtime._verified_ctranslate2_devices.cache_clear()


@pytest.mark.parametrize("failure", [OSError("missing runtime"), RuntimeError("no driver")])
def test_ctranslate2_cuda_probe_fails_closed_to_cpu(monkeypatch, failure):
    model_runtime._verified_ctranslate2_devices.cache_clear()
    monkeypatch.setattr(model_runtime, "_installed_package", lambda name: name == "ctranslate2")

    def fail_import(_name):
        raise failure

    monkeypatch.setattr(model_runtime, "import_module", fail_import)
    try:
        assert model_runtime._verified_ctranslate2_devices() == frozenset()
    finally:
        model_runtime._verified_ctranslate2_devices.cache_clear()


def test_current_environment_does_not_infer_accelerators_from_package_stubs(monkeypatch):
    stub = SimpleNamespace(backends=SimpleNamespace(
        mps=SimpleNamespace(is_available=lambda: False)
    ))
    _macos_probe_inputs(monkeypatch, stub, ["CPUExecutionProvider"])
    environment = capabilities.current_environment()
    assert environment.onnx_device == "cpu"
    assert environment.torch_device == "cpu"


def test_macos_current_environment_verifies_metal_and_discovers_coreml(monkeypatch):
    class Scalar:
        def __add__(self, other):
            return self

        def item(self):
            return 2

    def on_metal(_size, *, device):
        assert device == "mps"
        return Scalar()

    torch = SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        ones=on_metal,
    )
    _macos_probe_inputs(monkeypatch, torch, ["CoreMLExecutionProvider", "CPUExecutionProvider"])
    environment = capabilities.current_environment()
    assert environment.onnx_device == "coreml"
    assert environment.torch_device == "metal"
    assert capabilities.capability_for("nudenet", environment=environment).device == "coreml"
    gate = capabilities.capability_for("image_safety_gate", environment=environment)
    assert gate.device == "metal"


def _macos_probe_inputs(monkeypatch, torch, providers):
    monkeypatch.setattr(model_runtime.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(model_runtime.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(model_runtime, "_installed_package", lambda _name: True)
    for probe in ("_packaged_onnx_providers", "_verified_mps_devices"):
        monkeypatch.setattr(model_runtime, probe, getattr(model_runtime, probe).__wrapped__)
    modules = {
        "onnxruntime": SimpleNamespace(get_available_providers=lambda: providers),
        "torch": torch,
    }
    monkeypatch.setattr(model_runtime, "import_module", modules.__getitem__)


def test_windows_current_environment_falls_back_when_cuda_runtime_probe_fails(monkeypatch):
    model_runtime._packaged_onnx_providers.cache_clear()
    model_runtime._verified_ctranslate2_devices.cache_clear()
    monkeypatch.setattr(model_runtime.platform, "system", lambda: "Windows")
    monkeypatch.setattr(model_runtime.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(model_runtime, "_installed_package", lambda _name: True)
    monkeypatch.setattr(
        model_runtime,
        "import_module",
        lambda _name: (_ for _ in ()).throw(OSError("no compatible NVIDIA driver")),
    )

    try:
        environment = capabilities.current_environment()
        assert environment.ctranslate2_devices == frozenset()
        assert capabilities.capability_for("whisper", environment=environment).device == "cpu"
    finally:
        model_runtime._packaged_onnx_providers.cache_clear()
        model_runtime._verified_ctranslate2_devices.cache_clear()


def _detector_finding() -> FindingCreate:
    return FindingCreate(
        video_id="scan-1",
        category=McfCategoryFamily.VIOLENCE,
        subcategory="violence",
        start_seconds=1.0,
        end_seconds=2.0,
        severity=Severity.HIGH,
        confidence=0.9,
        label="detector-flag",
        details="Detector finding",
        group_key="violence",
        source="visual",
    )


def _deferred_qwen() -> ModelCapability:
    return ModelCapability(
        id="qwen3_vl",
        display_name="Qwen3-VL enrichment",
        stage="enrichment",
        availability="unavailable",
        reason="deferred_gap",
        device="none",
        weight_requirement="on_demand",
        download={"state": "not_downloaded"},
        enrichment_behavior="detector_findings_only",
    )


def _missing_qwen() -> ModelCapability:
    return _deferred_qwen().model_copy(
        update={"reason": "missing_package", "enrichment_behavior": "none"}
    )


def test_missing_macos_qwen_is_rejected_before_scan_work(monkeypatch, tmp_path: Path):
    video_path = tmp_path / "video.mp4"
    video_path.touch()
    calls: list[str] = []

    def require(backend_id: str):
        calls.append(backend_id)
        if backend_id == "qwen3_vl":
            raise capabilities.CapabilityUnavailableError("Qwen package missing")

    monkeypatch.setattr(runner, "require_available", require)
    monkeypatch.setattr(runner, "qwen_enrichment_capability", _missing_qwen)

    with pytest.raises(capabilities.CapabilityUnavailableError, match="Qwen package missing"):
        runner.run_scan(
            object(),
            str(video_path),
            scan_audio=False,
            scan_video=True,
            video_id="scan-1",
            status_updater=lambda *_args: pytest.fail("scan must not be admitted"),
            findings_writer=lambda *_args: pytest.fail("scan must not write findings"),
        )

    assert calls == ["nudenet", "qwen3_vl"]


def test_deferred_qwen_preserves_flagged_detector_findings(monkeypatch, tmp_path: Path):
    findings = [_detector_finding()]
    before = [finding.model_dump() for finding in findings]
    monkeypatch.setattr(visual, "qwen_enrichment_capability", _deferred_qwen)
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda _paths: pytest.fail("deferred Qwen must not be imported or called"),
    )

    result = visual.summarize_flagged_segments(
        findings,
        [(tmp_path / "flagged.jpg", 1.0)],
        summaries_per_segment=1,
        join_gap_seconds=0,
        replay_dir=tmp_path / "replay",
    )

    assert result is findings
    assert [finding.model_dump() for finding in result] == before
    assert not (tmp_path / "replay").exists()


def test_deferred_qwen_keeps_clean_scene_empty(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(visual, "qwen_enrichment_capability", _deferred_qwen)

    assert visual.summarize_flagged_segments(
        [],
        [(tmp_path / "clean.jpg", 1.0)],
        summaries_per_segment=1,
        join_gap_seconds=0,
    ) == []
