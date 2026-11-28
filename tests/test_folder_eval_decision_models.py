import base64
import dataclasses
import io
from pathlib import Path

import pytest

from scanner.evaluation.folder_eval.backends import BackendUnavailable, create_backend
from scanner.evaluation.folder_eval.decision.llama_backend import (
    LlamaServerDecisionBackend,
    image_long_edge,
)
from scanner.evaluation.folder_eval.decision.registry import BACKENDS
from scanner.evaluation.folder_eval.decision.schema import (
    FAST_FAMILIES,
    FAST_TOKEN_FAMILIES,
    build_questions,
    build_request,
    image_data_url,
    parse_response,
    supported_keys,
)
from scanner.evaluation.folder_eval.decision.specs import SPECS


def _recorded_response(questions, value_for):
    """Shape of a llama-server `/v1/systemone` response with noul answers."""

    return {
        "model": "d1-3B-Q8_0.gguf",
        "answers": {
            question.question_id: {"type": "noul", "noul": value_for(question.key)}
            for question in questions
        },
        "usage": {"input_tokens": 27000, "output_tokens": 0},
    }


def test_questions_cover_every_family_and_specific_token():
    questions = build_questions()
    keys = [question.key for question in questions]

    assert len(questions) == 97
    assert len({question.question_id for question in questions}) == 97
    assert {key for key in keys if "/" not in key} == {
        "commercial_content",
        "discrimination",
        "dispensable_scenes",
        "drugs",
        "fear",
        "language",
        "nudity",
        "sex",
        "violence",
    }
    assert "violence/violence" not in keys
    by_key = {question.key: question.text for question in questions}
    assert by_key["violence/stabbing"] == "Does this video frame show stabbing (violence)?"
    assert by_key["fear/hemophobia"] == "Does this video frame show blood or gore (fear)?"
    assert by_key["violence"] == "Does this video frame show violence?"

    supports = supported_keys(questions)
    assert len(supports) == 106
    assert {"violence/violence", "commercial_content/commercial", "fear/fear"} <= set(supports)


def test_build_request_sends_one_noul_question_per_key_with_the_image(tmp_path: Path):
    frame = tmp_path / "frame.JPG"
    payload = b"\xff\xd8jpeg"
    frame.write_bytes(payload)
    questions = build_questions()[:2]

    body = build_request(image_data_url(frame), questions, "A still frame from a video.")

    assert body["state"] == "A still frame from a video."
    assert body["images"] == [f"data:image/jpeg;base64,{base64.b64encode(payload).decode()}"]
    assert body["questions"] == {
        "q0": {"type": "noul", "instructions": questions[0].text},
        "q1": {"type": "noul", "instructions": questions[1].text},
    }
    assert build_request("data:image/png;base64,", questions)["state"] is None


def test_parse_response_maps_probabilities_and_mirrors_general_tokens():
    questions = build_questions()
    values = {"violence": 0.91, "violence/punching": 1.0000001, "fear": 0.2}
    body = _recorded_response(questions, lambda key: values.get(key, 0.01))

    scores = parse_response(body, questions)

    assert scores["violence"] == 0.91
    assert scores["violence/violence"] == 0.91
    assert scores["fear/fear"] == 0.2
    assert scores["violence/punching"] == 1.0
    assert scores["sex/kissing"] == 0.01
    assert set(scores) == set(supported_keys(questions))


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": 500, "message": "Compute error."}},
        {"answers": {"q0": {"type": "noul", "noul": 0.4}}},
        {"answers": {"q0": {"type": "noul", "noul": None}, "q1": {"noul": 0.3}}},
    ],
)
def test_parse_response_rejects_missing_answers(body):
    with pytest.raises(ValueError):
        parse_response(body, build_questions()[:2])


def _fake_backend(monkeypatch, tmp_path: Path, model_id: str, family_values: dict):
    """Backend whose server answers each family per frame name and 0.6 for every token."""

    backend = BACKENDS[model_id]()
    by_id = {question.question_id: question.key for question in backend.questions}
    bodies = []
    monkeypatch.setattr(backend, "_ensure_server", lambda: None)

    def post(body):
        bodies.append(body)
        image = base64.b64decode(body["images"][0].split(",", 1)[1]).decode()
        answers = {}
        for qid in body["questions"]:
            key = by_id[qid]
            value = 0.6 if "/" in key else family_values[image].get(key, 0.1)
            answers[qid] = {"type": "noul", "noul": value}
        return {"answers": answers}

    monkeypatch.setattr(backend, "_post", post)
    frames = []
    for name in family_values:
        frames.append(tmp_path / name)
        frames[-1].write_bytes(name.encode())
    return backend, bodies, frames


def test_backend_asks_tokens_only_for_families_over_the_stage2_threshold(monkeypatch, tmp_path):
    monkeypatch.delenv("EDITARR_DM_STAGE2_THRESHOLD", raising=False)
    backend, bodies, frames = _fake_backend(
        monkeypatch,
        tmp_path,
        "d1-3b",
        {"fight.jpg": {"violence": 0.8, "fear": 0.25}, "calm.jpg": {"violence": 0.2}},
    )

    fight, calm = backend.score_frames(frames)

    assert [len(body["questions"]) for body in bodies] == [9, 16 + 30, 9]
    assert fight["scores"]["violence"] == 0.8
    assert fight["scores"]["violence/violence"] == 0.8
    assert fight["scores"]["violence/punching"] == 0.6
    assert fight["scores"]["fear/hemophobia"] == 0.6
    assert "sex/kissing" not in fight["scores"]
    assert fight["asked"] == sorted(fight["scores"])
    assert fight["labels"] == []
    assert calm["scores"]["violence"] == 0.2
    assert len(calm["asked"]) == 18  # families + general aliases only


def test_clef_sends_each_stage2_family_separately_and_honours_env_threshold(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_DM_STAGE2_THRESHOLD", "0.5")
    backend, bodies, frames = _fake_backend(
        monkeypatch,
        tmp_path,
        "clef-flash",
        {"a.jpg": {"violence": 0.9, "nudity": 0.7, "fear": 0.4}},
    )

    (result,) = backend.score_frames(frames)

    assert [len(body["questions"]) for body in bodies] == [9, 4, 16]
    assert all(body["state"] == "A still frame from a video." for body in bodies)
    assert "fear/hemophobia" not in result["scores"]
    assert result["scores"]["nudity/toplessness"] == 0.6
    meta = backend.meta()
    assert meta["model_id"] == "clef-flash"
    assert meta["thresholds"] == {"*": 0.5}
    assert meta["stage2_threshold"] == 0.5
    assert len(meta["supports"]) == 106


def test_backend_reports_missing_weights_as_unavailable(monkeypatch, tmp_path: Path):
    server = tmp_path / "llama-server"
    server.write_text("")
    server.chmod(0o755)
    monkeypatch.setenv("EDITARR_LLAMA_SERVER", str(server))
    backend = LlamaServerDecisionBackend(SPECS["d1-3b"], models_dir=tmp_path)

    with pytest.raises(BackendUnavailable, match="unavailable: missing weights"):
        backend.score_frames([tmp_path / "frame.jpg"])


def test_backend_reports_missing_server_as_unavailable(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("EDITARR_LLAMA_SERVER", str(tmp_path / "missing"))
    backend = LlamaServerDecisionBackend(SPECS["d1-omni-2b"], models_dir=tmp_path)

    with pytest.raises(BackendUnavailable, match="unavailable: llama-server not found"):
        backend.score_frames([tmp_path / "frame.jpg"])


def test_server_command_uses_model_files_and_flags(tmp_path: Path):
    backend = LlamaServerDecisionBackend(SPECS["clef-flash"], models_dir=tmp_path)

    command = backend.server_command(9999)

    assert command[command.index("-m") + 1] == str(
        tmp_path / "clef-flash" / "Clef-Flash-Q4_K_M.gguf"
    )
    assert command[command.index("--mmproj") + 1].endswith("mmproj-Clef-Flash-Q8_0.gguf")
    assert command[command.index("--port") + 1] == "9999"
    from scanner.evaluation.folder_eval.decision.onejson_backends import (
        BACKENDS as ONEJSON_BACKENDS,
    )
    from scanner.evaluation.folder_eval.decision.qwen_mlx import SPECS as MLX_SPECS
    from scanner.evaluation.folder_eval.decision.qwen_vlm import SPECS as VLM_SPECS

    scan_ids = {"scan-d1-3b", "scan-qwen3-vl-8b", "scan-qwen3-vl-4b"}
    assert set(BACKENDS) == (
        set(SPECS)
        | set(VLM_SPECS)
        | set(MLX_SPECS)
        | set(ONEJSON_BACKENDS)
        | scan_ids
        | {"d1-3b-sev+qwen-desc"}
    )
    assert set(SPECS) == {
        f"{model}{suffix}"
        for model in ("d1-omni-2b", "d1-3b", "clef-flash")
        for suffix in ("", "-fast", "-f512")
    } | {"d1-3b-full4", "d1-3b-gore", "d1-omni-2b-gore", "clef-flash-gore", "d1-3b-sev"}


def test_create_backend_fails_fast_without_server(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("EDITARR_LLAMA_SERVER", str(tmp_path / "missing"))

    with pytest.raises(BackendUnavailable, match="llama-server not found"):
        create_backend("d1-3b")


def test_fast_question_set_asks_four_families_and_only_nudity_and_sex_tokens():
    questions = build_questions(FAST_FAMILIES, FAST_TOKEN_FAMILIES)
    keys = [question.key for question in questions]

    assert [key for key in keys if "/" not in key] == ["drugs", "nudity", "sex", "violence"]
    assert {key.split("/")[0] for key in keys if "/" in key} == {"nudity", "sex"}
    assert len(keys) == 4 + 4 + 10
    supports = supported_keys(questions)
    assert {"drugs/drugs", "nudity/nudity", "sex/sex", "violence/violence"} <= set(supports)
    assert not any(key.startswith(("fear", "language", "violence/punching")) for key in supports)
    assert BACKENDS["d1-3b-fast"]().meta()["supports"] == supports


def test_fast_backend_asks_tokens_only_for_triggered_nudity_and_sex(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    monkeypatch.delenv("EDITARR_DM_STAGE2_THRESHOLD", raising=False)
    backend, bodies, frames = _fake_backend(
        monkeypatch,
        tmp_path,
        "clef-flash-fast",
        {"a.jpg": {"violence": 0.9, "drugs": 0.9, "sex": 0.3}, "b.jpg": {"nudity": 0.5}},
    )

    first, second = backend.score_frames(frames)

    assert [len(body["questions"]) for body in bodies] == [4, 10, 4, 4]
    assert first["scores"]["violence/violence"] == 0.9
    assert first["scores"]["sex/coitus"] == 0.6 and "nudity/toplessness" not in first["scores"]
    assert second["scores"]["nudity/toplessness"] == 0.6 and "sex/coitus" not in second["scores"]


def test_single_stage_spec_asks_every_question_in_one_request(monkeypatch, tmp_path):
    import dataclasses

    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    spec = dataclasses.replace(SPECS["d1-3b-fast"], two_stage=False)
    backend, bodies, frames = _fake_backend(monkeypatch, tmp_path, "d1-3b-fast", {"a.jpg": {}})
    backend.spec = spec

    (result,) = backend.score_frames(frames)

    assert [len(body["questions"]) for body in bodies] == [18]
    assert len(result["asked"]) == 22 and result["scores"]["sex/coitus"] == 0.6


def test_image_data_url_downscales_large_frames_in_memory(tmp_path):
    from PIL import Image

    large, small = tmp_path / "large.png", tmp_path / "small.jpg"
    Image.new("RGB", (1000, 500), "red").save(large)
    Image.new("RGB", (300, 200), "blue").save(small)
    original = large.read_bytes()

    url = image_data_url(large, 384)

    assert url.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
        assert image.size == (384, 192)
    assert large.read_bytes() == original
    assert image_data_url(small, 384) == image_data_url(small)
    assert image_data_url(large).startswith("data:image/png;base64,")


def test_image_long_edge_env_overrides_only_fast_specs(monkeypatch):
    monkeypatch.delenv("EDITARR_DM_IMAGE_LONG_EDGE", raising=False)
    assert image_long_edge(SPECS["d1-3b-fast"]) == 448
    assert image_long_edge(SPECS["d1-3b"]) is None
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "512")
    assert image_long_edge(SPECS["clef-flash-fast"]) == 512
    assert image_long_edge(SPECS["clef-flash"]) is None
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    assert image_long_edge(SPECS["d1-omni-2b-fast"]) is None


def test_fast_backends_share_the_base_weights(tmp_path):
    fast = LlamaServerDecisionBackend(SPECS["d1-omni-2b-fast"], models_dir=tmp_path)
    assert fast.weights_dir == tmp_path / "d1-omni-2b"


@pytest.mark.parametrize("base_id", ["d1-omni-2b", "d1-3b", "clef-flash"])
def test_f512_specs_ask_only_the_four_families_once_at_512_px(monkeypatch, tmp_path, base_id):
    monkeypatch.delenv("EDITARR_DM_IMAGE_LONG_EDGE", raising=False)
    spec, fast = SPECS[f"{base_id}-f512"], SPECS[f"{base_id}-fast"]
    backend = BACKENDS[f"{base_id}-f512"]()

    assert [question.key for question in backend.questions] == list(FAST_FAMILIES)
    assert backend.meta()["supports"] == [
        "drugs",
        "drugs/drugs",
        "nudity",
        "nudity/nudity",
        "sex",
        "sex/sex",
        "violence",
        "violence/violence",
    ]
    assert image_long_edge(spec) == 512
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "384")
    assert image_long_edge(spec) == 384
    assert spec.bundled_server and not spec.two_stage
    assert (spec.server_args, spec.default_concurrency, spec.model_file) == (
        fast.server_args,
        fast.default_concurrency,
        fast.model_file,
    )
    assert LlamaServerDecisionBackend(spec, models_dir=tmp_path).weights_dir == tmp_path / base_id


def test_f512_backend_sends_one_four_question_request_per_frame(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    backend, bodies, frames = _fake_backend(
        monkeypatch, tmp_path, "d1-3b-f512", {"a.jpg": {"nudity": 0.9, "sex": 0.8}}
    )

    (result,) = backend.score_frames(frames)

    assert [len(body["questions"]) for body in bodies] == [4]
    assert result["scores"]["nudity"] == 0.9 and result["scores"]["nudity/nudity"] == 0.9
    assert len(result["asked"]) == 8 and "nudity/toplessness" not in result["scores"]


def test_full4_is_f512_without_downscaling_unless_overridden(monkeypatch, tmp_path):
    from PIL import Image

    monkeypatch.delenv("EDITARR_DM_IMAGE_LONG_EDGE", raising=False)
    spec, f512 = SPECS["d1-3b-full4"], SPECS["d1-3b-f512"]
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (768, 432), "red").save(frame)

    assert (
        dataclasses.replace(
            spec, model_id=f512.model_id, display_name=f512.display_name, image_long_edge=512
        )
        == f512
    )
    assert image_long_edge(spec) is None
    assert (
        BACKENDS["d1-3b-full4"]().meta()["supports"] == BACKENDS["d1-3b-f512"]().meta()["supports"]
    )
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "512")
    assert image_long_edge(spec) == 512
    assert image_data_url(frame, image_long_edge(spec)) != image_data_url(frame)


@pytest.mark.parametrize(
    ("model_id", "base_id", "edge"),
    [
        ("d1-3b-gore", "d1-3b-full4", None),
        ("d1-omni-2b-gore", "d1-omni-2b-f512", 512),
        ("clef-flash-gore", "clef-flash-f512", 512),
    ],
)
def test_gore_specs_ask_one_blood_question_with_their_base_settings(
    monkeypatch, model_id, base_id, edge
):
    monkeypatch.delenv("EDITARR_DM_IMAGE_LONG_EDGE", raising=False)
    spec, base = SPECS[model_id], SPECS[base_id]
    backend = BACKENDS[model_id]()

    assert [(q.key, q.text) for q in backend.questions] == [
        ("fear/hemophobia", "Does this frame show blood or gore?")
    ]
    assert backend.meta()["supports"] == ["fear/hemophobia"]
    assert image_long_edge(spec) == edge
    assert not spec.two_stage and spec.bundled_server
    settings = ("server_args", "default_concurrency", "model_file", "weights_id", "state")
    assert all(getattr(spec, name) == getattr(base, name) for name in settings)


def test_gore_backend_returns_only_the_hemophobia_score(monkeypatch, tmp_path):
    monkeypatch.setenv("EDITARR_DM_IMAGE_LONG_EDGE", "0")
    backend, bodies, frames = _fake_backend(monkeypatch, tmp_path, "clef-flash-gore", {"a.jpg": {}})

    (result,) = backend.score_frames(frames)

    assert bodies[0]["questions"] == {
        "q0": {"type": "noul", "instructions": "Does this frame show blood or gore?"}
    }
    assert result["scores"] == {"fear/hemophobia": 0.6}
    assert result["asked"] == ["fear/hemophobia"]
