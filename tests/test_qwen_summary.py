import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scanner.evaluation.subcategorization import SubcategorizationFallbackReason
from scanner.models import McfCategoryFamily
from scanner.pipeline import qwen_summary
from scanner.settings.capabilities import CapabilityUnavailableError


@pytest.fixture(autouse=True)
def _available_qwen(monkeypatch):
    """Exercise summary mechanics independently of the test host's target matrix."""

    monkeypatch.setattr(qwen_summary, "require_available", lambda _backend_id: None)


def test_qwen_summary_uses_the_requested_prompt():
    assert "For each category detected" in qwen_summary.PROMPT
    assert "sampled frames from one flagged video segment" in qwen_summary.PROMPT
    assert "across any of the frames" in qwen_summary.PROMPT

    prompt = qwen_summary.subcategorization_prompt((McfCategoryFamily.NUDITY,))

    assert "Return exactly one JSON object" in prompt
    assert "nudity: nudity, bareButtocks, exposedGenitalia, fullNudity, toplessness" in prompt
    assert "violence: violence" not in prompt


def test_qwen_summary_extracts_categories_for_video_review_grouping():
    response = "Categories: Violence, blood/gore, and weapons. A person holds a gun."

    assert qwen_summary._categories_from_response(response) == (
        "violence",
        "blood/gore",
        "weapons",
    )


def test_qwen_malformed_constrained_output_retains_the_family_fallback(monkeypatch):
    dependencies = {
        "mlx_vlm": SimpleNamespace(
            generate=lambda *_args, **_kwargs: SimpleNamespace(text="not JSON")
        ),
        "mlx_vlm.prompt_utils": SimpleNamespace(
            apply_chat_template=lambda *_args, **_kwargs: "formatted prompt"
        ),
    }
    monkeypatch.setattr(
        qwen_summary,
        "_load_model",
        lambda _qwen_model: (object(), object(), object()),
    )
    monkeypatch.setattr(
        qwen_summary,
        "_require_dependency",
        lambda import_name, _install_hint: dependencies[import_name],
    )

    summary = qwen_summary.summarize_frames(
        [Path("/tmp/flagged-frame.jpg")],
        families=(McfCategoryFamily.NUDITY,),
    )

    assert summary.valid_subcategorization_output is False
    assert summary.subcategorization[0].subcategory.value == "nudity"
    assert (
        summary.subcategorization[0].fallback_reason
        == SubcategorizationFallbackReason.INVALID_OUTPUT
    )


def test_qwen_summary_uses_the_mlx_4bit_model_with_all_segment_frames(monkeypatch):
    calls = {}
    processor = object()
    model = object()
    config = object()

    def apply_chat_template(actual_processor, actual_config, prompt, *, num_images):
        calls["template"] = (actual_processor, actual_config, prompt, num_images)
        return "formatted prompt"

    def generate(actual_model, actual_processor, prompt, **kwargs):
        calls["generate"] = (actual_model, actual_processor, prompt, kwargs)
        return SimpleNamespace(
            text=json.dumps(
                {
                    "version": 1,
                    "objectionable": True,
                    "summary": "Two people are fighting.",
                    "classifications": [
                        {
                            "family": "violence",
                            "subcategory": "punching",
                            "confidence": 0.96,
                            "evidence": "One person punches another.",
                        }
                    ],
                }
            )
        )

    dependencies = {
        "mlx_vlm": SimpleNamespace(generate=generate),
        "mlx_vlm.prompt_utils": SimpleNamespace(apply_chat_template=apply_chat_template),
    }
    monkeypatch.setattr(
        qwen_summary,
        "_load_model",
        lambda _qwen_model: (model, processor, config),
    )
    monkeypatch.setattr(
        qwen_summary,
        "_require_dependency",
        lambda import_name, _install_hint: dependencies[import_name],
    )

    summary = qwen_summary.summarize_frames(
        [
            Path("/tmp/flagged-frame-1.jpg"),
            Path("/tmp/flagged-frame-2.jpg"),
        ],
        families=(McfCategoryFamily.VIOLENCE,),
    )

    assert qwen_summary.DEFAULT_QWEN_MODEL == "qwen3-vl-8b-instruct-4bit"
    expected_prompt = qwen_summary.subcategorization_prompt(
        (McfCategoryFamily.VIOLENCE,)
    )
    assert calls["template"] == (processor, config, expected_prompt, 2)
    assert calls["generate"] == (
        model,
        processor,
        "formatted prompt",
        {
            "image": [
                str(Path("/tmp/flagged-frame-1.jpg").resolve()),
                str(Path("/tmp/flagged-frame-2.jpg").resolve()),
            ],
            "max_tokens": 160,
            "temperature": 0.0,
            "verbose": False,
        },
    )
    assert summary.categories == ("punching",)
    assert summary.explanation == "Two people are fighting."
    assert summary.valid_subcategorization_output is True
    assert summary.subcategorization[0].subcategory.value == "violence"
    assert (
        summary.subcategorization[0].fallback_reason
        == SubcategorizationFallbackReason.INSUFFICIENT_EVIDENCE
    )


def test_qwen_summary_requires_at_least_one_frame():
    try:
        qwen_summary.summarize_frames([])
    except ValueError as exc:
        assert str(exc) == "At least one frame is required"
    else:
        raise AssertionError("Expected an empty segment to be rejected")


def test_qwen_summary_rejects_unavailable_backend_before_importing_mlx(monkeypatch):
    def unavailable(_backend_id):
        raise CapabilityUnavailableError("Qwen3-VL enrichment is unavailable: deferred gap")

    monkeypatch.setattr(qwen_summary, "require_available", unavailable)
    monkeypatch.setattr(
        qwen_summary,
        "_require_dependency",
        lambda *_args: pytest.fail("MLX dependencies must not be imported"),
    )

    with pytest.raises(CapabilityUnavailableError, match="deferred gap"):
        qwen_summary.summarize_frames([Path("/tmp/flagged-frame.jpg")])


def test_qwen_model_download_root_is_the_resolved_regenerable_cache(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(qwen_summary.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(qwen_summary, "_CACHE", {})

    managed_path = tmp_path / "models" / "artifacts" / "qwen"
    monkeypatch.setattr(
        qwen_summary,
        "acquire_artifact",
        lambda artifact_id: calls.update({"artifact_id": artifact_id}) or managed_path,
    )

    dependencies = {
        "mlx_vlm": SimpleNamespace(
            load=lambda path: calls.update({"load_path": path}) or ("model", "processor")
        ),
        "mlx_vlm.utils": SimpleNamespace(
            load_config=lambda path: calls.update({"config_path": path}) or "config"
        ),
    }
    monkeypatch.setattr(
        qwen_summary,
        "_require_dependency",
        lambda import_name, _install_hint: dependencies[import_name],
    )

    assert qwen_summary._load_model() == ("model", "processor", "config")
    assert calls == {
        "artifact_id": "qwen3-vl-8b-instruct-4bit",
        "load_path": str(managed_path),
        "config_path": str(managed_path),
    }


def test_qwen_summary_selects_4b_model_and_records_its_provenance(monkeypatch):
    response = json.dumps(
        {
            "version": 1,
            "objectionable": True,
            "summary": "A weapon is visible.",
            "classifications": [
                {
                    "family": "violence",
                    "subcategory": "weapons",
                    "confidence": 0.95,
                    "evidence": "A person holds a gun.",
                }
            ],
        }
    )
    dependencies = {
        "mlx_vlm": SimpleNamespace(
            generate=lambda *_args, **_kwargs: SimpleNamespace(text=response)
        ),
        "mlx_vlm.prompt_utils": SimpleNamespace(
            apply_chat_template=lambda *_args, **_kwargs: "formatted prompt"
        ),
    }
    selected = []
    monkeypatch.setattr(
        qwen_summary,
        "_load_model",
        lambda qwen_model: selected.append(qwen_model) or (object(), object(), object()),
    )
    monkeypatch.setattr(
        qwen_summary,
        "_require_dependency",
        lambda import_name, _install_hint: dependencies[import_name],
    )

    summary = qwen_summary.summarize_frames(
        [Path("/tmp/flagged-frame.jpg")],
        families=(McfCategoryFamily.VIOLENCE,),
        qwen_model="qwen3-vl-4b-instruct-4bit",
    )

    assert selected == ["qwen3-vl-4b-instruct-4bit"]
    assert summary.subcategorization[0].model_id == (
        "mlx-community/Qwen3-VL-4B-Instruct-4bit"
    )
    assert summary.subcategorization[0].model_revision == (
        "2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b"
    )
