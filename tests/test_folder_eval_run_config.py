"""Resumed runs never mix scoring configurations."""

from __future__ import annotations

import json

import pytest

from scanner.evaluation.folder_eval import backends, runner
from scanner.evaluation.folder_eval.run_config import (
    ConfigMismatch,
    fingerprint,
    scoring_config,
    stored_fingerprints,
)
from tests.folder_eval_support import cached_layout, clip


class _Backend:
    model_id = "fake"

    def __init__(self, threshold=0.5, edge=768, events=None):
        self.threshold, self.edge, self.events = threshold, edge, events
        self.spec = ("spec", edge)
        self.questions = ["Is violence shown?"]

    def meta(self):
        return {"model_id": "fake", "supports": ["violence"], "thresholds": {"*": self.threshold},
                "image_long_edge": self.edge, "notes": f"run at {id(self)}"}

    def score_frames(self, paths):
        return [{"scores": {"violence": 0.9}} for _ in paths]

    def close(self):
        if self.events is not None:
            self.events.append("closed")


def _layout(tmp_path, count=2):
    items = [clip(f"i{n}", "video/x", ["violence"], frames=1) for n in range(count)]
    layout = cached_layout(tmp_path, *items)
    layout.inventory.write_text(json.dumps({"items": items}), encoding="utf-8")
    return layout


def _lines(layout):
    text = layout.model_jsonl("fake").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def test_fingerprint_covers_scoring_settings_but_not_display_text():
    base = fingerprint(scoring_config(_Backend()))
    assert fingerprint(scoring_config(_Backend())) == base  # notes differ, scores do not
    assert fingerprint(scoring_config(_Backend(threshold=0.6))) != base
    assert fingerprint(scoring_config(_Backend(edge=0))) != base
    other = _Backend()
    other.questions = ["Is a weapon shown?"]
    assert fingerprint(scoring_config(other)) != base


def test_runs_record_the_fingerprint_and_refuse_to_resume_another(tmp_path, monkeypatch):
    layout = _layout(tmp_path)
    monkeypatch.setattr(runner, "create_backend", lambda _id: _Backend())
    runner.run_model(layout, "fake", limit=1)
    expected = fingerprint(scoring_config(_Backend()))
    assert [line["config"] for line in _lines(layout)] == [expected]
    meta = json.loads(layout.model_meta("fake").read_text())
    assert meta["config_fingerprint"] == expected
    assert meta["config"]["meta"]["image_long_edge"] == 768

    events = []
    monkeypatch.setattr(runner, "create_backend", lambda _id: _Backend(edge=0, events=events))
    with pytest.raises(ConfigMismatch, match="another configuration.*--restart"):
        runner.run_model(layout, "fake")
    assert len(_lines(layout)) == 1 and events == ["closed"]


def test_lines_without_a_fingerprint_require_a_restart(tmp_path, monkeypatch):
    layout = _layout(tmp_path)
    layout.models.mkdir(parents=True)
    layout.model_jsonl("fake").write_text(
        json.dumps({"id": "i0", "status": "ok", "frames": [{"n": 0, "scores": {}}]}) + "\n"
    )
    monkeypatch.setattr(runner, "create_backend", lambda _id: _Backend())
    with pytest.raises(ConfigMismatch, match="without a scoring-configuration fingerprint"):
        runner.run_model(layout, "fake")


def test_cli_restart_archives_and_all_skips_mismatched_models(tmp_path, monkeypatch, caplog):
    from scanner.commands import folder_eval_cli as cli

    layout = _layout(tmp_path)
    layout.models.mkdir(parents=True)
    layout.model_jsonl("fake").write_text(json.dumps({"id": "i0", "status": "ok"}) + "\n")
    layout.model_meta("fake").write_text("{}")
    monkeypatch.setenv("EDITARR_WORKER_MODEL_DOWNLOADS", "")
    monkeypatch.setattr(backends, "_downloads_allowed", False)
    monkeypatch.setattr(runner, "create_backend", lambda _id: _Backend())
    for name in ("write_inventory", "extract_frames", "summarize", "install_viewer"):
        monkeypatch.setattr(cli, name, lambda *_a, **_k: None)
    base = ["--data-root", str(tmp_path)]

    assert cli.main([*base, "all", "--models", "fake"]) == 0
    assert "Skipping fake" in caplog.text and len(_lines(layout)) == 1
    assert cli.main([*base, "run", "--model", "fake"]) == 2
    assert cli.main([*base, "run", "--model", "fake", "--restart"]) == 0
    archived = sorted(path.name for path in layout.models.glob("fake.*.bak"))
    assert len(archived) == 2 and archived[0].startswith("fake.jsonl.")
    assert {line["config"] for line in _lines(layout)} == {fingerprint(scoring_config(_Backend()))}
    assert len(_lines(layout)) == 2


def test_summary_flags_results_that_mix_configurations(tmp_path):
    from scanner.evaluation.folder_eval.summary import summarize

    layout = _layout(tmp_path)
    layout.models.mkdir(parents=True)
    lines = [{"id": "i0", "status": "ok", "frames": [], "flagged": [], "config": "aaa"},
             {"id": "i1", "status": "ok", "frames": [], "flagged": [], "config": "bbb"}]
    layout.model_jsonl("fake").write_text("".join(json.dumps(line) + "\n" for line in lines))
    assert stored_fingerprints(layout.model_jsonl("fake")) == {"aaa", "bbb"}
    model = summarize(layout)["models"]["fake"]
    assert model["mixed_configs"] is True and model["config_fingerprints"] == ["aaa", "bbb"]


def test_restarts_in_the_same_instant_keep_every_archive(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from scanner.evaluation.folder_eval import run_config
    from scanner.evaluation.folder_eval.paths import Layout

    frozen = datetime(2026, 11, 27, 12, 0, 0, 123456, tzinfo=timezone.utc)
    clock = type("Frozen", (), {"now": staticmethod(lambda tz: frozen)})
    monkeypatch.setattr(run_config, "datetime", clock)
    layout = Layout(tmp_path)
    layout.models.mkdir(parents=True)
    archives = []
    for run in range(3):
        layout.model_jsonl("m").write_text(f"lines {run}\n")
        layout.model_meta("m").write_text(f"meta {run}")
        archives.append(run_config.archive_results(layout, "m"))
    names = [[path.name for path in pair] for pair in archives]
    stem = "20261127T120000.123456Z"
    assert names[0] == [f"m.jsonl.{stem}.bak", f"m.meta.json.{stem}.bak"]
    assert names[1][0].endswith(".123456Z-1.bak") and names[2][1].endswith(".123456Z-2.bak")
    for run, (jsonl, meta) in enumerate(archives):  # matched pairs, none overwritten
        assert jsonl.read_text() == f"lines {run}\n" and meta.read_text() == f"meta {run}"
    assert not layout.model_jsonl("m").exists() and not layout.model_meta("m").exists()

    # A taken meta name must not split the pair: both move to the next stem.
    layout.model_jsonl("m").write_text("lines 3\n")
    layout.model_meta("m").write_text("meta 3")
    (layout.models / "m.meta.json.20261127T120000.123456Z-3.bak").write_text("unrelated")
    jsonl, meta = run_config.archive_results(layout, "m")
    assert jsonl.name.endswith("-4.bak") and meta.name.endswith("-4.bak")
    assert (layout.models / "m.meta.json.20261127T120000.123456Z-3.bak").read_text() == "unrelated"
    assert not (layout.models / "m.jsonl.20261127T120000.123456Z-3.bak").exists()
