"""Shieldstral screening, routing, detections, and fine-pass dispatch without weights."""

import pytest
from PIL import Image

from scanner.models import McfCategoryFamily, Severity
from scanner.pipeline import shieldstral, visual
from scanner.pipeline.processes import ScanCancelledError
from scanner.pipeline.shieldstral import FrameResult, evaluate_frame, smooth_sexual_runs
from scanner.pipeline.shieldstral_policies import (
    NUDITY_LABEL,
    SCREENS,
    SEXUAL_LABEL,
    SEXUAL_ROUTES,
    VIOLENCE_LABEL,
    route_sexual_labels,
)
from scanner.settings.config import settings

QUERIES = {policy.id: policy.query for policy in SCREENS + SEXUAL_ROUTES}


def test_normalize_video_model_accepts_shieldstral_alias():
    assert visual.normalize_video_model("shieldstral") == "shieldstral"
    assert visual.normalize_video_model("Shieldstral-1.0-3B") == "shieldstral"


def test_screens_and_routes_use_the_evaluated_defaults():
    assert [(screen.id, screen.threshold) for screen in SCREENS] == [
        ("sexual", 0.4),
        ("violence", 0.4),
    ]
    assert settings.shieldstral_max_image_height == 288


@pytest.mark.parametrize(
    ("routes", "expected"),
    [
        ({"nudity": 0.1, "sex_act": 0.2}, (SEXUAL_LABEL,)),
        ({"nudity": 0.25, "sex_act": 0.49}, (NUDITY_LABEL,)),
        ({"nudity": 0.2, "sex_act": 0.6}, (SEXUAL_LABEL,)),
        ({"nudity": 0.9, "sex_act": 0.6}, (NUDITY_LABEL, SEXUAL_LABEL)),
        ({}, (SEXUAL_LABEL,)),
    ],
)
def test_sexual_routing_chooses_families(routes, expected):
    assert route_sexual_labels(routes) == expected


def test_routing_thresholds_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "shieldstral_nudity_route_threshold", 0.3)
    monkeypatch.setattr(settings, "shieldstral_sex_route_threshold", 0.95)
    assert route_sexual_labels({"nudity": 0.35, "sex_act": 0.9}) == (NUDITY_LABEL,)


def _scorer(scores: dict[str, float], asked: list[str]):
    by_query = {QUERIES[policy_id]: value for policy_id, value in scores.items()}

    def score(query):
        asked.append(query)
        return by_query.get(query, 0.0)

    return score


def test_routes_are_asked_only_for_sexual_positive_frames():
    asked: list[str] = []
    result = evaluate_frame(_scorer({"sexual": 0.39, "violence": 0.8}, asked))

    assert asked == [QUERIES["sexual"], QUERIES["violence"]]
    assert result.routes == {}
    assert result.labels() == {VIOLENCE_LABEL: 0.8}

    asked.clear()
    result = evaluate_frame(_scorer({"sexual": 0.4, "nudity": 0.9}, asked))
    assert asked == [QUERIES["sexual"], QUERIES["violence"], QUERIES["nudity"], QUERIES["sex_act"]]
    assert result.labels() == {NUDITY_LABEL: 0.4}


def test_scan_frame_uses_screen_scores_as_confidence(monkeypatch, tmp_path):
    result = FrameResult(
        {"sexual": 0.9, "violence": 0.7}, {"nudity": 0.99, "sex_act": 0.51}
    )
    monkeypatch.setattr(shieldstral, "score_frame", lambda _path: result)

    detections = visual.scan_frame(tmp_path / "f.jpg", 12.5, video_model="shieldstral")

    assert [
        (item.label, item.category, item.confidence, item.severity, item.timestamp)
        for item in detections
    ] == [
        (NUDITY_LABEL, McfCategoryFamily.NUDITY, 0.9, Severity.HIGH, 12.5),
        (SEXUAL_LABEL, McfCategoryFamily.SEX, 0.9, Severity.HIGH, 12.5),
        (VIOLENCE_LABEL, McfCategoryFamily.VIOLENCE, 0.7, Severity.MEDIUM, 12.5),
    ]
    assert [item.subcategory for item in detections] == ["nudity", "sex", "violence"]


def test_fine_scan_refines_routed_family_changes(monkeypatch, tmp_path):
    frames = [(tmp_path / f"frame_{index:04d}.jpg", index / 24) for index in range(48)]
    index_of = {path: index for index, (path, _timestamp) in enumerate(frames)}
    calls = []

    def score_frame(path):
        index = index_of[path]
        calls.append(index)
        if not 10 <= index < 40:
            return FrameResult({"sexual": 0.1, "violence": 0.1})
        routes = {"nudity": 0.9 if index < 25 else 0.1, "sex_act": 0.9 if index >= 32 else 0.1}
        return FrameResult({"sexual": 0.8, "violence": 0.1}, routes)

    monkeypatch.setattr(shieldstral, "score_frame", score_frame)
    monkeypatch.setattr(settings, "shieldstral_fine_sample_interval_seconds", 0.5)
    progress = []

    findings = visual.scan_frames(
        frames,
        "video-1",
        video_model="shieldstral",
        frame_interval_seconds=1 / 24,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    by_label = {
        label: [finding.start_seconds for finding in findings if finding.label == label]
        for label in (NUDITY_LABEL, SEXUAL_LABEL)
    }
    # Unknown-family frames 25-31 join the nudity run; sex-act frames keep SEX.
    assert by_label[NUDITY_LABEL] == [timestamp for _path, timestamp in frames[10:32]]
    assert by_label[SEXUAL_LABEL] == [timestamp for _path, timestamp in frames[32:40]]
    assert len(calls) < len(frames) / 2
    assert progress == [(index, 48) for index in range(1, 49)]


def _sexual_run_frames(monkeypatch, tmp_path, count, *, nudity_at=None):
    """Score an all-sexual-positive clip of unknown family, optionally one nudity frame."""
    frames = [(tmp_path / f"frame_{index:04d}.jpg", index / 24) for index in range(count)]
    index_of = {path: index for index, (path, _timestamp) in enumerate(frames)}
    scored: list[int] = []

    def score_frame(path):
        scored.append(index_of[path])
        return _N if index_of[path] == nudity_at else _F

    monkeypatch.setattr(shieldstral, "score_frame", score_frame)
    monkeypatch.setattr(settings, "shieldstral_fine_sample_interval_seconds", 0.5)
    return frames, scored


def test_fine_scan_reports_progress_while_a_sexual_run_is_buffered(monkeypatch, tmp_path):
    frames, _scored = _sexual_run_frames(monkeypatch, tmp_path, 96, nudity_at=95)
    progress: list[tuple[int, int, int]] = []
    emitted: list[int] = []

    def on_resolved(done):
        progress.append((done, len(emitted)))

    for detections in shieldstral.scan_frames(frames, 1 / 24, on_resolved=on_resolved):
        emitted.append(len(detections))

    # The whole clip is one run, so nothing is emitted until its last frame resolves.
    assert progress == [(done, 0) for done in range(1, 97)]
    # The final nudity frame still resolves the run's unknown-family frames.
    findings = visual.scan_frames(
        frames, "video-1", video_model="shieldstral", frame_interval_seconds=1 / 24
    )
    assert {finding.label for finding in findings} == {NUDITY_LABEL}
    assert len(findings) == 96


def test_fine_scan_progress_can_stop_scoring_mid_run(monkeypatch, tmp_path):
    frames, scored = _sexual_run_frames(monkeypatch, tmp_path, 96)
    progress: list[tuple[int, int]] = []

    class Refused(RuntimeError):
        pass

    def progress_callback(done, total):
        progress.append((done, total))
        if done == 24:
            raise Refused

    with pytest.raises(Refused):
        visual.scan_frames(
            frames,
            "video-1",
            video_model="shieldstral",
            frame_interval_seconds=1 / 24,
            progress_callback=progress_callback,
        )

    assert progress == [(done, 96) for done in range(1, 25)]
    assert max(scored) < 48


def test_fine_scan_honors_cancellation_mid_run(monkeypatch, tmp_path):
    frames, scored = _sexual_run_frames(monkeypatch, tmp_path, 96)
    checks: list[int] = []

    def should_cancel():
        checks.append(len(scored))
        return len(checks) > 30

    with pytest.raises(ScanCancelledError):
        visual.scan_frames(
            frames,
            "video-1",
            video_model="shieldstral",
            frame_interval_seconds=1 / 24,
            should_cancel=should_cancel,
        )

    assert len(checks) == 31
    assert max(scored) < 48


@pytest.mark.parametrize(
    ("interval", "sample_interval", "expected"),
    [(1 / 24, 0.5, 12), (1 / 30, 0.5, 15), (1.0, 0.5, 1), (0.0, 0.5, 1)],
)
def test_fine_stride_rounds_to_whole_source_frames(
    monkeypatch, interval, sample_interval, expected
):
    monkeypatch.setattr(settings, "shieldstral_fine_sample_interval_seconds", sample_interval)
    assert shieldstral.fine_stride(interval) == expected


def test_load_frame_downscales_to_an_even_width_preserving_aspect(tmp_path):
    tall, short = tmp_path / "tall.png", tmp_path / "short.png"
    Image.new("RGBA", (910, 512)).save(tall)
    Image.new("L", (320, 240)).save(short)

    resized = shieldstral.load_frame(tall)
    unchanged = shieldstral.load_frame(short, max_height=288)

    assert (resized.size, resized.mode) == ((512, 288), "RGB")
    assert (unchanged.size, unchanged.mode) == ((320, 240), "RGB")


_N = FrameResult({"sexual": 0.8, "violence": 0.1}, {"nudity": 0.9, "sex_act": 0.1})
_F = FrameResult({"sexual": 0.8, "violence": 0.1}, {"nudity": 0.2, "sex_act": 0.1})
_FV = FrameResult({"sexual": 0.8, "violence": 0.9}, {"nudity": 0.2, "sex_act": 0.1})
_S = FrameResult({"sexual": 0.8, "violence": 0.1}, {"nudity": 0.1, "sex_act": 0.9})
_C = FrameResult({"sexual": 0.1, "violence": 0.1})
_V = FrameResult({"sexual": 0.1, "violence": 0.9})
_NUD, _SEX, _VIO = {NUDITY_LABEL}, {SEXUAL_LABEL}, {VIOLENCE_LABEL}


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([_C, _F, _N, _F, _C], [set(), _NUD, _NUD, _NUD, set()]),
        ([_C, _F, _F, _C], [set(), _SEX, _SEX, set()]),
        ([_F, _S, _N, _F], [_NUD, _SEX, _NUD, _NUD]),
        ([_F, _N, _V, _F, _F, _C, _N], [_NUD, _NUD, _VIO, _SEX, _SEX, set(), _NUD]),
        ([_V, _FV, _N, _C], [_VIO, _NUD | _VIO, _NUD, set()]),
        ([], []),
    ],
)
def test_unknown_family_frames_take_nudity_from_their_sexual_run(results, expected):
    smoothed = list(smooth_sexual_runs(results))

    assert [set(result.labels()) for result in smoothed] == expected


def test_run_smoothing_buffers_only_inside_sexual_runs():
    consumed = []

    def source():
        for result in (_C, _V, _F, _N, _C):
            consumed.append(result)
            yield result

    stream = smooth_sexual_runs(source())
    assert next(stream) is _C and len(consumed) == 1
    assert next(stream) is _V and len(consumed) == 2
    assert set(next(stream).labels()) == _NUD and len(consumed) == 5


def test_unknown_family_edges_are_refined_separately_from_sex_acts():
    assert _F.key() != _S.key()
    assert set(_F.labels()) == set(_S.labels()) == _SEX
