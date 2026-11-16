import json
import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from scanner.credits.credits_detector import ChapterMarker, QwenAssessment, default_configuration
from scanner.credits.credits_media import (
    analyze_sampled_frames,
    assess_frames_with_qwen,
    detect_credits_in_media,
    read_container_chapters,
)


def _write_frame(path: Path, lines: list[str], *, offset: int = 0):
    image = np.zeros((240, 420), dtype=np.uint8)
    for index, line in enumerate(lines):
        cv2.putText(
            image,
            line,
            (40, 40 + index * 32 + offset),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            255,
            2,
            cv2.LINE_AA,
        )
    assert cv2.imwrite(str(path), image)


def test_visual_analysis_finds_text_motion_and_black_frames(tmp_path: Path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    black = tmp_path / "black.png"
    lines = ["CAST AND CREW", "ALEX EXAMPLE", "SAM SAMPLE", "EDITARR STUDIOS"]
    _write_frame(first, lines)
    _write_frame(second, lines, offset=-12)
    assert cv2.imwrite(str(black), np.zeros((240, 420), dtype=np.uint8))

    observations, _scenes = analyze_sampled_frames(
        [(first, 60.0), (second, 62.0), (black, 64.0)]
    )

    assert observations[0].text_density >= 0.5
    assert observations[1].credit_motion >= 0.5
    assert observations[2].black_frame >= 0.85


def test_container_chapters_ignore_incomplete_metadata(monkeypatch):
    class Container:
        chapters = [
            SimpleNamespace(
                start=900,
                time_base=Fraction(1, 10),
                metadata={"title": "End Credits"},
            ),
            SimpleNamespace(start=None, time_base=None, metadata={}),
        ]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=lambda _path: Container()))

    assert read_container_chapters("generated.mkv") == [
        ChapterMarker(start_seconds=90.0, title="End Credits")
    ]


def test_container_chapters_support_callable_pyav_api(monkeypatch):
    chapter = SimpleNamespace(
        start=900,
        time_base=Fraction(1, 10),
        metadata={"title": "End Credits"},
    )

    class Container:
        def chapters(self):
            return [chapter]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=lambda _path: Container()))

    assert read_container_chapters("generated.mkv") == [
        ChapterMarker(start_seconds=90.0, title="End Credits")
    ]


def test_qwen_assessment_is_strict_and_uses_existing_summary(monkeypatch, tmp_path: Path):
    frame = tmp_path / "frame.png"
    _write_frame(frame, ["CREDITS"])
    response = {
        "credits_visible": True,
        "confidence": 0.83,
        "boundary_frame_index": 0,
        "evidence": "sustained cast and crew list",
    }
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda *_args, **_kwargs: SimpleNamespace(explanation=json.dumps(response)),
    )

    assert assess_frames_with_qwen(
        [frame], [72.0], qwen_model="qwen3-vl-4b-instruct-4bit"
    ) == [QwenAssessment(72.0, 0.83, "sustained cast and crew list")]


def test_qwen_assessment_rejects_malformed_output_on_every_platform(
    monkeypatch, tmp_path: Path
):
    frame = tmp_path / "frame.png"
    _write_frame(frame, ["CREDITS"])
    monkeypatch.setattr(
        "scanner.pipeline.qwen_summary.summarize_frames",
        lambda *_args, **_kwargs: SimpleNamespace(explanation="not json"),
    )

    assert assess_frames_with_qwen(
        [frame], [72.0], qwen_model="qwen3-vl-4b-instruct-4bit"
    ) == []


def test_media_adapter_is_bounded_and_does_not_call_qwen_when_disabled(
    monkeypatch, tmp_path: Path
):
    frame = tmp_path / "frame.png"
    _write_frame(frame, ["CAST AND CREW", "ALEX EXAMPLE", "SAM SAMPLE"])
    captured = {}

    def sample(_path, scene, _workspace, interval, **_kwargs):
        captured["scene"] = scene
        captured["interval"] = interval
        return [(frame, scene.start_seconds)]

    monkeypatch.setattr("scanner.credits.credits_media.sample_scene_frames", sample)
    monkeypatch.setattr(
        "scanner.credits.credits_media.read_container_chapters",
        lambda _path: [SimpleNamespace(start_seconds=75.0, title="End Credits")],
    )

    result = detect_credits_in_media(
        "generated.mkv",
        duration_seconds=100.0,
        configuration=default_configuration(),
        workspace=tmp_path,
        qwen_assessor=lambda *_args: (_ for _ in ()).throw(AssertionError("Qwen called")),
    )

    assert captured["scene"].start_seconds == 55.0
    assert captured["scene"].end_seconds == 100.0
    assert captured["interval"] == 2.0
    assert result.candidates[0].boundary_seconds == 75.0


def test_media_adapter_continues_without_qwen_when_no_ready_assessor(
    monkeypatch, tmp_path: Path
):
    frame = tmp_path / "frame.png"
    _write_frame(frame, ["CAST AND CREW", "ALEX EXAMPLE", "SAM SAMPLE"])
    monkeypatch.setattr(
        "scanner.credits.credits_media.sample_scene_frames",
        lambda *_args, **_kwargs: [(frame, 75.0)],
    )
    monkeypatch.setattr(
        "scanner.credits.credits_media.read_container_chapters",
        lambda _path: [SimpleNamespace(start_seconds=75.0, title="End Credits")],
    )

    result = detect_credits_in_media(
        "generated.mkv",
        duration_seconds=100.0,
        configuration=default_configuration(qwen_model="qwen3-vl-4b-instruct-4bit"),
        workspace=tmp_path,
    )

    assert result.candidates[0].method != "hybrid_qwen"
