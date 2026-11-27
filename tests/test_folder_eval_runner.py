"""Tests for resumable, concurrent model runs and their result lines and metadata."""

from __future__ import annotations

import json
import os

import pytest

from scanner.evaluation.folder_eval.paths import Layout
from scanner.evaluation.folder_eval.results import load_results
from scanner.evaluation.folder_eval.summary import summarize_model
from tests.folder_eval_support import clip


def test_merge_record_combines_frames_and_keeps_ok_over_error(tmp_path):
    lines = [
        {"id": "a", "status": "ok", "frames": [{"n": 2, "scores": {"nudity": 0.7}}],
         "flagged": ["nudity"]},
        {"id": "a", "status": "ok", "frames": [{"n": 0, "scores": {"nudity": 0.1}}],
         "flagged": []},
        {"id": "a", "status": "error", "error": "boom", "frames": []},
    ]
    path = tmp_path / "m.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    record = load_results(path)["a"]
    assert [frame["n"] for frame in record["frames"]] == [0, 2]
    assert record["scores"] == {"nudity": 0.7} and record["flagged"] == ["nudity"]
    assert record["status"] == "ok" and record["last_error"] == "boom"


def test_run_subset_scores_only_missing_subset_frames(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval import runner

    layout = Layout(tmp_path)
    item = clip("a", "video/x", ["violence"])
    for frame in item["frames"]:
        (layout.results / frame["file"]).parent.mkdir(parents=True, exist_ok=True)
        (layout.results / frame["file"]).write_bytes(b"x")
    layout.inventory.write_text(json.dumps({"items": [item]}), encoding="utf-8")
    layout.models.mkdir(parents=True)
    layout.model_jsonl("fake").write_text(json.dumps(
        {"id": "a", "status": "ok", "frames": [{"n": 1, "scores": {"violence": 0.2}}]}
    ) + "\n", encoding="utf-8")
    calls: list[list[str]] = []

    class Fake:
        model_id = "fake"

        def meta(self):
            return {"model_id": "fake", "supports": ["violence"], "thresholds": {"*": 0.5}}

        def score_frames(self, paths):
            calls.append([path.name for path in paths])
            return [{"scores": {"violence": 0.8}} for _ in paths]

        def close(self):
            pass

    monkeypatch.setattr(runner, "create_backend", lambda _model_id: Fake())
    runner.run_model(layout, "fake", subset={"a": [1, 2]})
    runner.run_model(layout, "fake", subset={"a": [1, 2]})
    assert calls == [["2.jpg"]]
    record = load_results(layout.model_jsonl("fake"))["a"]
    assert [frame["n"] for frame in record["frames"]] == [1, 2]
    assert record["flagged"] == ["violence"] and record["scored_n"] == [2]


def _runner_layout(tmp_path):
    layout = Layout(tmp_path)
    items = [clip(identifier, "video/x", ["violence"]) for identifier in ("a", "b")]
    for item in items:
        for frame in item["frames"]:
            (layout.results / frame["file"]).parent.mkdir(parents=True, exist_ok=True)
            (layout.results / frame["file"]).write_bytes(b"x")
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")
    layout.models.mkdir(parents=True)
    return layout


class _UnavailableBackend:
    model_id = "fake"
    calls = 0

    def meta(self):
        return {"model_id": "fake", "supports": ["violence"], "thresholds": {"*": 0.5}}

    def score_frames(self, paths):
        from scanner.evaluation.folder_eval.backends import BackendUnavailable

        type(self).calls += 1
        raise BackendUnavailable("unavailable: llama-server exited during startup")

    def close(self):
        pass


def test_run_aborts_without_error_lines_when_backend_unavailable(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval import runner
    from scanner.evaluation.folder_eval.backends import BackendUnavailable

    layout = _runner_layout(tmp_path)
    monkeypatch.setattr(runner, "create_backend", lambda _model_id: _UnavailableBackend())

    with pytest.raises(BackendUnavailable):
        runner.run_model(layout, "fake")

    assert _UnavailableBackend.calls == 1
    output = layout.model_jsonl("fake")
    assert not output.exists() or output.read_text() == ""
    assert not layout.model_meta("fake").exists()


def test_run_fails_fast_when_backend_creation_reports_unavailable(tmp_path, monkeypatch):
    from scanner.commands import folder_eval_cli as cli
    from scanner.evaluation.folder_eval import runner
    from scanner.evaluation.folder_eval.backends import BackendUnavailable

    layout = _runner_layout(tmp_path)

    def unavailable(_model_id):
        raise BackendUnavailable("decision backends unavailable")

    monkeypatch.setattr(runner, "create_backend", unavailable)
    code = cli.main(["--data-root", str(tmp_path), "run", "--model", "fake"])
    assert code == 2 and not layout.model_jsonl("fake").exists()


def test_run_retries_items_recorded_as_unavailable_by_older_runs(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval import runner

    layout = _runner_layout(tmp_path)
    layout.model_jsonl("fake").write_text(
        json.dumps({"id": "a", "status": "error", "error": "RuntimeError: unavailable: x"})
        + "\n"
        + json.dumps({"id": "b", "status": "error", "error": "ValueError: bad answer"})
        + "\n",
        encoding="utf-8",
    )

    class Working(_UnavailableBackend):
        def score_frames(self, paths):
            return [{"scores": {"violence": 0.9}} for _ in paths]

    monkeypatch.setattr(runner, "create_backend", lambda _model_id: Working())
    stats = runner.run_model(layout, "fake")
    results = load_results(layout.model_jsonl("fake"))
    assert stats["items"] == 1
    assert results["a"]["status"] == "ok" and results["b"]["status"] == "error"


def test_summary_timing_uses_only_newly_scored_frames():
    items = [{"id": "a", "path": "video/x/a.mp4", "expected": [], "scored": True}]
    results = {"a": {
        "id": "a", "status": "ok", "elapsed_ms": 30.0, "scored_n": [3], "flagged": [],
        "frames": [{"n": n, "scores": {}} for n in range(4)],
    }}
    summary = summarize_model(items, results, {"supports": ["violence"]})
    assert summary["mean_ms_per_frame"] == 30.0 and summary["frames"] == 4


def _many_items_layout(tmp_path, count):
    layout = Layout(tmp_path)
    items = [clip(f"i{index}", "video/x", ["violence"], frames=1) for index in range(count)]
    for item in items:
        for frame in item["frames"]:
            (layout.results / frame["file"]).parent.mkdir(parents=True, exist_ok=True)
            (layout.results / frame["file"]).write_bytes(b"x")
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")
    layout.models.mkdir(parents=True)
    return layout


class _CountingBackend:
    """Records the most items scored at once; `barrier` forces that many to overlap."""

    model_id = "fake"

    def __init__(self, default_concurrency=None, barrier=None):
        import threading

        if default_concurrency is not None:
            self.default_concurrency = default_concurrency
        self.barrier = barrier
        self.lock = threading.Lock()
        self.active = self.peak = 0

    def meta(self):
        return {"model_id": "fake", "supports": ["violence"], "thresholds": {"*": 0.5}}

    def score_frames(self, paths):
        import time

        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.barrier is not None:
                self.barrier.wait()
            time.sleep(0.01)
            return [{"scores": {"violence": 0.9}, "asked": ["violence"]} for _ in paths]
        finally:
            with self.lock:
                self.active -= 1

    def close(self):
        pass


def test_run_concurrency_scores_items_in_parallel_with_one_line_each(tmp_path, monkeypatch):
    import threading

    from scanner.evaluation.folder_eval import runner

    layout = _many_items_layout(tmp_path, 9)
    backend = _CountingBackend(default_concurrency=1, barrier=threading.Barrier(3, timeout=5))
    monkeypatch.setattr(runner, "create_backend", lambda _model_id: backend)

    stats = runner.run_model(layout, "fake", concurrency=3)

    text = layout.model_jsonl("fake").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line]
    assert sorted(json.loads(line)["id"] for line in lines) == [f"i{n}" for n in range(9)]
    assert backend.peak == 3 and stats["concurrency"] == 3 and stats["frames"] == 9
    assert json.loads(lines[0])["frames"][0]["asked"] == ["violence"]


def test_run_uses_backend_default_concurrency_and_ignores_it_for_other_backends(
    tmp_path, monkeypatch
):
    from scanner.evaluation.folder_eval import runner

    layout = _many_items_layout(tmp_path, 6)
    decision = _CountingBackend(default_concurrency=2)
    monkeypatch.setattr(runner, "create_backend", lambda _model_id: decision)
    assert runner.run_model(layout, "fake")["concurrency"] == 2

    (layout.models / "fake.jsonl").unlink()
    detector = _CountingBackend()
    monkeypatch.setattr(runner, "create_backend", lambda _model_id: detector)
    assert runner.run_model(layout, "fake", concurrency=4)["concurrency"] == 1
    assert detector.peak == 1


def test_run_concurrency_aborts_when_backend_becomes_unavailable(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval import runner
    from scanner.evaluation.folder_eval.backends import BackendUnavailable

    layout = _many_items_layout(tmp_path, 8)

    class Unavailable(_CountingBackend):
        def score_frames(self, paths):
            raise BackendUnavailable("unavailable: llama-server exited during startup")

    monkeypatch.setattr(runner, "create_backend", lambda _model_id: Unavailable(4))

    with pytest.raises(BackendUnavailable):
        runner.run_model(layout, "fake", concurrency=4)
    output = layout.model_jsonl("fake")
    assert not output.exists() or output.read_text() == ""


def test_throughput_totals_accumulate_and_seed_from_last_run():
    from scanner.evaluation.folder_eval.run_meta import accumulate_throughput, wall_ms_per_frame

    previous = {"last_run": {"frames": 16, "elapsed_ms": 5453.1, "wall_ms_per_frame": 340.8}}
    stats = {"frames": 4, "elapsed_ms": 1000.0, "wall_ms_per_frame": 250.0}
    totals = accumulate_throughput(previous, stats)
    assert totals == {"wall_ms": 6453.1, "frames": 20, "runs": 2}
    assert wall_ms_per_frame({"throughput": totals}) == 322.66
    assert accumulate_throughput({}, {"frames": 0, "elapsed_ms": 5.0}) == {
        "wall_ms": 0.0, "frames": 0, "runs": 0,
    }
    assert wall_ms_per_frame({"last_run": {"ms_per_frame": 9.0}}) is None


def test_summary_prefers_wall_clock_throughput_over_item_latency():
    from scanner.evaluation.folder_eval.summary import summarize_subset

    items = [{"id": "a", "path": "video/x/a.mp4", "expected": [], "scored": True}]
    results = {"a": {"id": "a", "status": "ok", "elapsed_ms": 600.0, "flagged": [],
                     "frames": [{"n": 0, "scores": {}}, {"n": 1, "scores": {}}]}}
    concurrent = {"supports": ["violence"], "throughput": {"wall_ms": 340.0, "frames": 2}}

    summary = summarize_model(items, results, concurrent)
    assert summary["mean_ms_per_frame"] == 170.0 and summary["latency_ms_per_frame"] == 300.0
    assert summary["timing_source"] == "wall_clock"
    subset = summarize_subset(items, results, concurrent, {"a": [0]})
    assert subset["mean_s_per_frame"] == 0.17 and subset["latency_s_per_frame"] == 0.3

    fallback = summarize_model(items, results, {"supports": ["violence"]})
    assert fallback["mean_ms_per_frame"] == fallback["latency_ms_per_frame"] == 300.0
    assert fallback["timing_source"] == "per_item_latency"


def test_run_records_backend_runtime_on_every_line(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval import runner

    layout = _many_items_layout(tmp_path, 3)
    backend = _CountingBackend(default_concurrency=2)
    backend.runtime = "llama.cpp b12556"
    monkeypatch.setattr(runner, "create_backend", lambda _model_id: backend)

    runner.run_model(layout, "fake")

    text = layout.model_jsonl("fake").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line]
    assert [json.loads(line)["runtime"] for line in lines] == ["llama.cpp b12556"] * 3


def test_append_after_a_torn_line_keeps_the_next_record(tmp_path):
    from scanner.evaluation.folder_eval.results import append_line

    path = tmp_path / "m.jsonl"
    path.write_text(json.dumps({"id": "a", "status": "ok"}) + '\n{"id": "b", "sta')
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        append_line(descriptor, {"id": "c", "status": "ok"})
    finally:
        os.close(descriptor)
    assert set(load_results(path)) == {"a", "c"}


def test_describe_eval_defaults_to_the_subset_film_subset_writes():
    from scanner.commands.folder_eval_cli import _parser

    parser = _parser()
    written = parser.parse_args(["film-subset"]).name
    assert parser.parse_args(["describe-eval"]).subset == written


def test_meta_records_server_and_process_memory_separately(tmp_path, monkeypatch):
    from scanner.evaluation.folder_eval import runner

    layout = _runner_layout(tmp_path)

    class Served:
        model_id = "fake"
        peak_rss_bytes = 3 * 2**30

        def meta(self):
            return {"model_id": "fake", "supports": ["violence"], "thresholds": {"*": 0.5}}

        def score_frames(self, paths):
            return [{"scores": {"violence": 0.1}} for _ in paths]

        def close(self):
            pass

    monkeypatch.setattr(runner, "create_backend", lambda _model_id: Served())
    runner.run_model(layout, "fake")
    meta = json.loads(layout.model_meta("fake").read_text())
    assert meta["server_peak_rss_mb"] == 3072.0 and meta["process_peak_rss_mb"] > 0
    assert "peak_rss_mb" not in meta
    summary = summarize_model([], {}, meta)
    assert summary["server_peak_rss_mb"] == 3072.0
    assert summary["process_peak_rss_mb"] == meta["process_peak_rss_mb"]
    legacy = summarize_model([], {}, {"peak_rss_mb": 12.5})
    assert (legacy["process_peak_rss_mb"], legacy["server_peak_rss_mb"]) == (12.5, None)
