import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.pipeline import frames, image_safety_gate, siglip2_moderation, visual


def test_normalize_video_model_accepts_aliases():
    assert visual.normalize_video_model("nudenet") == "nudenet"
    assert visual.normalize_video_model("image_safety_gate") == "image_safety_gate"
    assert visual.normalize_video_model("image-safety-gate") == "image_safety_gate"
    assert visual.normalize_video_model("siglip2_moderation_nsfw") == "siglip2_moderation_nsfw"
    assert (
        visual.normalize_video_model("siglip2-moderation-violence")
        == "siglip2_moderation_violence"
    )
    assert visual.normalize_video_model("comfyui-image-safety-gate") == "image_safety_gate"


def test_normalize_video_model_rejects_unknown_model():
    with pytest.raises(ValueError, match="Unsupported video model"):
        visual.normalize_video_model("unknown-stack")


def test_scan_frame_routes_to_selected_model(monkeypatch, tmp_path):
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"test")

    calls: list[str] = []

    def fake_nudenet(_frame_path, _timestamp):
        calls.append("nudenet")
        return []

    def fake_gate(_frame_path, _timestamp):
        calls.append("gate")
        return []

    monkeypatch.setattr("scanner.pipeline.visual._scan_frame_nudenet", fake_nudenet)
    monkeypatch.setattr("scanner.pipeline.visual._scan_frame_image_safety_gate", fake_gate)

    visual.scan_frame(frame_path, 1.0)
    visual.scan_frame(frame_path, 1.0, video_model="image_safety_gate")

    assert calls == ["nudenet", "gate"]


def test_scan_frame_routes_to_the_requested_siglip2_moderation_head(monkeypatch, tmp_path):
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"test")
    heads: list[str] = []

    def fake_siglip2(_frame_path, _timestamp, head_name):
        heads.append(head_name)
        return []

    monkeypatch.setattr(
        "scanner.pipeline.visual._scan_frame_siglip2_moderation", fake_siglip2
    )

    visual.scan_frame(frame_path, 1.0, video_model="siglip2_moderation_nsfw")
    visual.scan_frame(frame_path, 2.0, video_model="siglip2_moderation_violence")

    assert heads == ["nsfw", "violence"]


def test_siglip2_uses_the_pooled_embedding_from_transformers_model_output():
    embedding = object()

    assert (
        siglip2_moderation._image_embedding(SimpleNamespace(pooler_output=embedding))
        is embedding
    )
    assert siglip2_moderation._image_embedding(embedding) is embedding


def test_visual_model_roots_derive_from_the_resolved_model_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(image_safety_gate.settings, "model_cache", tmp_path / "models")

    assert image_safety_gate._model_root() == tmp_path / "models" / "image_safety_gate"
    assert siglip2_moderation._model_root() == tmp_path / "models" / "siglip2_moderation"


def test_siglip2_scan_frames_batches_the_encoder_calls(monkeypatch, tmp_path):
    frame_paths = []
    for index in range(3):
        frame_path = tmp_path / f"frame-{index}.jpg"
        frame_path.write_bytes(b"test")
        frame_paths.append((frame_path, float(index)))
    calls = []
    progress = []
    monkeypatch.setattr(visual.settings, "visual_batch_size", 2)
    monkeypatch.setattr(
        siglip2_moderation,
        "score_images",
        lambda paths, head_name: calls.append(([path.name for path in paths], head_name))
        or ([0.4, 0.9] if len(paths) == 2 else [0.8]),
    )
    monkeypatch.setattr(siglip2_moderation, "threshold_for_head", lambda _head_name: 0.5)

    findings = visual.scan_frames(
        frame_paths,
        "video-1",
        video_model="siglip2_moderation_violence",
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert calls == [
        (["frame-0.jpg", "frame-1.jpg"], "violence"),
        (["frame-2.jpg"], "violence"),
    ]
    assert [(finding.start_seconds, finding.category.value) for finding in findings] == [
        (1.0, "violence"),
        (2.0, "violence"),
    ]
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_nudenet_scan_frames_uses_its_batch_api(monkeypatch, tmp_path):
    frame_paths = []
    for index in range(3):
        frame_path = tmp_path / f"frame-{index}.jpg"
        frame_path.write_bytes(b"test")
        frame_paths.append((frame_path, float(index)))
    calls = []

    class Detector:
        def detect_batch(self, paths, *, batch_size):
            calls.append(([Path(path).name for path in paths], batch_size))
            return [
                [{"class": "FEMALE_BREAST_EXPOSED", "score": 0.9}]
                for _path in paths
            ]

    monkeypatch.setattr(visual.settings, "visual_batch_size", 2)
    monkeypatch.setattr(visual, "_get_detector", Detector)

    findings = visual.scan_frames(frame_paths, "video-1", video_model="nudenet")

    assert calls == [(["frame-0.jpg", "frame-1.jpg"], 2), (["frame-2.jpg"], 1)]
    assert [finding.start_seconds for finding in findings] == [0.0, 1.0, 2.0]
    assert {finding.subcategory.value for finding in findings} == {"nudity"}


def test_nudenet_exact_body_part_labels_use_specific_mcf_subcategories(monkeypatch, tmp_path):
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"test")

    class Detector:
        def detect_batch(self, _paths, *, batch_size):
            assert batch_size == 1
            return [[
                {"class": "BUTTOCKS_EXPOSED", "score": 0.91},
                {"class": "FEMALE_GENITALIA_EXPOSED", "score": 0.93},
            ]]

    monkeypatch.setattr(visual, "_get_detector", Detector)

    findings = visual.scan_frames([(frame_path, 0.0)], "video-1", video_model="nudenet")

    assert [finding.subcategory.value for finding in findings] == [
        "bareButtocks",
        "exposedGenitalia",
    ]


def test_image_safety_gate_scan_frames_runs_gate_stages_serially(monkeypatch, tmp_path):
    frame_paths = []
    for index in range(3):
        frame_path = tmp_path / f"frame-{index}.jpg"
        frame_path.write_bytes(b"test")
        frame_paths.append((frame_path, float(index)))
    calls = []
    safe = image_safety_gate.ImageSafetyGateResult(0, 0, 0, 0, 0, 0, False, False, False)
    nsfw = image_safety_gate.ImageSafetyGateResult(0.5, 0.4, 0.9, 0, 0, 0, True, False, False)
    violence = image_safety_gate.ImageSafetyGateResult(0, 0, 0, 0, 0, 0.9, False, False, True)

    def scan_one(path, timestamp):
        calls.append(path.name)
        if path.name == "frame-0.jpg":
            result = nsfw
        elif path.name == "frame-2.jpg":
            result = violence
        else:
            result = safe
        return visual._image_safety_gate_to_detections(timestamp, result)

    monkeypatch.setattr(visual, "_scan_frame_image_safety_gate", scan_one)

    findings = visual.scan_frames(frame_paths, "video-1", video_model="image_safety_gate")

    assert calls == ["frame-0.jpg", "frame-1.jpg", "frame-2.jpg"]
    assert [(finding.start_seconds, finding.category.value) for finding in findings] == [
        (0.0, "nudity"),
        (2.0, "violence"),
    ]


def test_extracted_frames_default_to_a_512_pixel_height_ceiling():
    assert frames.extracted_frame_scale_filter() == "scale=-2:min(512\\,ih)"


def test_nudenet_cpu_capability_ignores_host_accelerators(monkeypatch):
    received = {}

    class FakeDetector:
        def __init__(self, *, providers):
            received["providers"] = providers

    fake_onnxruntime = SimpleNamespace(
        get_available_providers=lambda: [
            "CUDAExecutionProvider",
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ]
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_onnxruntime)
    monkeypatch.setitem(sys.modules, "nudenet", SimpleNamespace(NudeDetector=FakeDetector))
    monkeypatch.setattr(
        visual,
        "capability_for",
        lambda _backend_id: SimpleNamespace(device="cpu"),
    )
    monkeypatch.setattr(visual, "_detector", None)

    visual._get_detector()

    assert received["providers"] == ["CPUExecutionProvider"]


def test_nudenet_replaces_upstream_session_for_coreml(monkeypatch, tmp_path):
    old_session = object()
    detector = SimpleNamespace(input_width=320, input_height=320, onnx_session=old_session)
    ort = SimpleNamespace()
    coreml_session = object()
    calls = []
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "nudenet", SimpleNamespace(NudeDetector=lambda **_: detector))
    monkeypatch.setattr(visual, "_detector", None)
    monkeypatch.setattr(visual.settings, "model_cache", tmp_path)
    monkeypatch.setattr(visual, "capability_for", lambda _id: SimpleNamespace(device="coreml"))
    monkeypatch.setattr(visual, "files", lambda _package: tmp_path)

    def create_coreml(runtime, path, **options):
        calls.append((runtime, path, options))
        return coreml_session

    monkeypatch.setattr(visual, "create_coreml_session", create_coreml)
    assert visual._get_detector().onnx_session is coreml_session
    assert visual._get_detector() is detector
    assert calls == [(ort, tmp_path / "320n.onnx", {
        "dimensions": {"height": 320, "width": 320},
        "cache_dir": tmp_path / "nudenet" / "coreml-cache",
    })]


@pytest.mark.parametrize(
    "selector",
    [image_safety_gate._select_torch_device, siglip2_moderation._select_torch_device],
)
def test_cpu_capability_ignores_available_torch_accelerators(monkeypatch, selector):
    module = (
        image_safety_gate
        if selector is image_safety_gate._select_torch_device
        else siglip2_moderation
    )
    monkeypatch.setattr(
        module,
        "capability_for",
        lambda _backend_id: SimpleNamespace(device="cpu"),
    )
    fake_torch = SimpleNamespace(
        device=lambda name: name,
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        cuda=SimpleNamespace(is_available=lambda: True),
    )

    assert selector(fake_torch) == "cpu"


def test_siglip_head_cpu_capability_ignores_host_accelerators(monkeypatch, tmp_path):
    received = {}
    fake_onnxruntime = SimpleNamespace(
        get_available_providers=lambda: [
            "CUDAExecutionProvider",
            "CoreMLExecutionProvider",
            "CPUExecutionProvider",
        ],
        InferenceSession=lambda path, *, providers: received.update(
            {"path": path, "providers": providers}
        )
        or object(),
    )
    monkeypatch.setattr(siglip2_moderation, "_CACHE", {})
    monkeypatch.setattr(siglip2_moderation, "_model_root", lambda: tmp_path)
    monkeypatch.setattr(
        siglip2_moderation,
        "_require_dependency",
        lambda name, _hint: {
            "onnxruntime": fake_onnxruntime,
        }[name],
    )
    monkeypatch.setattr(
        siglip2_moderation,
        "get_acquisition_service",
        lambda: SimpleNamespace(acquire=lambda _artifact_id: tmp_path),
    )
    monkeypatch.setattr(
        siglip2_moderation,
        "capability_for",
        lambda _backend_id: SimpleNamespace(device="cpu"),
    )

    siglip2_moderation._load_head("nsfw")

    assert received["providers"] == ["CPUExecutionProvider"]


def test_scan_frames_caps_a_final_visual_finding_at_the_video_end(monkeypatch, tmp_path):
    frame_path = tmp_path / "frame.jpg"
    frame_path.write_bytes(b"test")
    detection = visual.VisualDetection(
        timestamp=9.5,
        label="Visual content",
        confidence=0.9,
        category=visual.McfCategoryFamily.NUDITY,
        subcategory="nudity",
        severity=visual.Severity.HIGH,
    )
    monkeypatch.setattr(visual, "_scan_frames_nudenet", lambda _frames: iter([[detection]]))
    findings = visual.scan_frames(
        [(frame_path, 9.5)],
        "video-1",
        frame_interval_seconds=15,
        video_duration_seconds=10,
    )

    assert findings[0].start_seconds == 9.5
    assert findings[0].end_seconds == 10
    # Detector findings carry no description; label and category identify them.
    assert findings[0].details is None
    assert findings[0].label == "Visual content"
    assert findings[0].group_key == "nudity"


def test_clip_loader_reads_the_checkpoint_once(monkeypatch, tmp_path):
    class FakeTensor:
        def to(self, _device):
            return self

    class FakeVisionModel:
        def __init__(self, _config):
            self.loaded_state: dict[str, FakeTensor] = {}

        def load_state_dict(self, state, strict):
            assert strict is True
            self.loaded_state = state
            return SimpleNamespace(missing_keys=[], unexpected_keys=[])

        def to(self, _device):
            return self

        def eval(self):
            return self

    state_dict = {
        "vision_model.embeddings": FakeTensor(),
        "concept_embeds": FakeTensor(),
        "concept_embeds_weights": FakeTensor(),
        "visual_projection.weight": FakeTensor(),
    }
    load_calls: list[object] = []
    fake_torch = SimpleNamespace(
        load=lambda path, map_location: load_calls.append((path, map_location)) or state_dict,
        device=lambda name: name,
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        cuda=SimpleNamespace(is_available=lambda: False),
    )
    fake_transformers = SimpleNamespace(
        CLIPImageProcessor=SimpleNamespace(from_pretrained=lambda _path: "processor"),
        CLIPVisionConfig=SimpleNamespace(from_pretrained=lambda _path: "config"),
        CLIPVisionModel=FakeVisionModel,
    )

    def fake_dependency(import_name, _install_hint):
        return {"torch": fake_torch, "transformers": fake_transformers}[import_name]

    monkeypatch.setattr(image_safety_gate, "_require_dependency", fake_dependency)
    monkeypatch.setattr(image_safety_gate, "_ensure_files", lambda *_args: tmp_path)
    image_safety_gate._CACHE.clear()

    try:
        model, *_rest = image_safety_gate._load_clip_model()

        assert len(load_calls) == 1
        assert model.loaded_state == {"embeddings": state_dict["vision_model.embeddings"]}
    finally:
        image_safety_gate._CACHE.clear()


def test_clip_loader_extracts_nested_safety_checker_vision_weights():
    weight = object()

    assert image_safety_gate._vision_state_dict({
        "vision_model.vision_model.embeddings.class_embedding": weight,
        "vision_model.vision_model.embeddings.position_ids": object(),
    }) == {"embeddings.class_embedding": weight}


def test_wd_rating_indices_use_data_row_positions_for_rating_tags(tmp_path):
    tags = tmp_path / "selected_tags.csv"
    tags.write_text(
        "tag_id,name,category,count\n"
        '1,"general",9,10\n'
        '2,"smile, open mouth",0,5\n'
        "3,sensitive,9.0,4\n"
        "4,not_rating,,3\n"
        "5,questionable,9,2\n"
        '6,"explicit",9,1\n',
        encoding="utf-8-sig",
    )

    assert image_safety_gate._wd_rating_indices(tags) == {
        "general": 0,
        "sensitive": 2,
        "questionable": 4,
        "explicit": 5,
    }
