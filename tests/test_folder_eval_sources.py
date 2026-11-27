"""Tests for the data-root default and the local film and ground-truth sources."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scanner.commands import folder_eval_cli
from scanner.evaluation.folder_eval import films, paths
from scanner.evaluation.folder_eval.film_sources import (
    GROUND_TRUTH_ENV,
    LOCAL_CONFIG_ENV,
    FilmSourceError,
    parse_film_options,
    resolve_sources,
)
from scanner.evaluation.folder_eval.paths import Layout

MOVIES = (
    "imdb_id,name,title_main,year,duration_sec\n"
    "tt1,Alpha,Alpha,2001,30\ntt2,Beta,Beta,2002,20\n"
)
TIMESTAMPS = (
    "imdb_id,category,tag_name,start_approx,end_approx\n"
    "tt1,violence,gore,12,13\ntt9,violence,gore,1,2\n"
)


def _ground_truth(directory: Path) -> Path:
    directory.mkdir(parents=True)
    (directory / "movies.csv").write_text(MOVIES, encoding="utf-8")
    (directory / "tag_timestamps.csv").write_text(TIMESTAMPS, encoding="utf-8")
    return directory


@pytest.fixture
def clean_env(monkeypatch):
    for name in (GROUND_TRUTH_ENV, LOCAL_CONFIG_ENV, paths.DATA_ROOT_ENV):
        monkeypatch.delenv(name, raising=False)


def test_data_root_defaults_to_the_checkout_and_honours_the_environment(
    monkeypatch, tmp_path, clean_env
):
    assert paths.default_data_root() == paths.REPO_ROOT / "evaluation_data"
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "site-packages")
    monkeypatch.chdir(tmp_path)
    assert paths.default_data_root() == tmp_path / "evaluation_data"
    monkeypatch.setenv(paths.DATA_ROOT_ENV, str(tmp_path / "elsewhere"))
    assert paths.default_data_root() == tmp_path / "elsewhere"


def test_sources_follow_cli_then_environment_then_local_config(monkeypatch, tmp_path, clean_env):
    layout = Layout(tmp_path / "data")
    local_truth = _ground_truth(tmp_path / "data" / "csv")
    (tmp_path / "data" / "films.local.json").write_text(
        json.dumps({"ground_truth_dir": "csv", "films": {"tt1": "movies/a.mkv"}}),
        encoding="utf-8",
    )
    sources = resolve_sources(layout)
    assert sources.ground_truth_dir == local_truth
    assert sources.config_files == {"tt1": tmp_path / "data" / "movies" / "a.mkv"}

    env_truth = _ground_truth(tmp_path / "env")
    monkeypatch.setenv(GROUND_TRUTH_ENV, str(env_truth))
    assert resolve_sources(layout).ground_truth_dir == env_truth

    cli_truth = _ground_truth(tmp_path / "cli")
    sources = resolve_sources(
        layout, ground_truth_dir=cli_truth, film_files=parse_film_options(["tt1=/x/b.mkv"])
    )
    assert sources.ground_truth_dir == cli_truth and sources.cli_files["tt1"] == Path("/x/b.mkv")


def test_sources_fall_back_to_the_copied_ground_truth_and_report_gaps(tmp_path, clean_env):
    layout = Layout(tmp_path)
    with pytest.raises(FilmSourceError, match="No ground-truth directory"):
        resolve_sources(layout)
    with pytest.raises(FilmSourceError, match="does not exist"):
        resolve_sources(layout, local_config=tmp_path / "missing.json")
    with pytest.raises(FilmSourceError, match="KEY=PATH"):
        parse_film_options(["tt1"])
    copy = _ground_truth(tmp_path / "films" / "ground_truth")
    sources = resolve_sources(layout)
    assert sources.ground_truth_dir == copy
    with pytest.raises(FilmSourceError, match="--film tt1=PATH"):
        sources.file_for("tt1", "Alpha (2001)")


def _films_config(tmp_path: Path) -> Path:
    config = tmp_path / "films.json"
    config.write_text(
        json.dumps({"films": [{"title": "Alpha", "year": 2001}, {"title": "Beta", "year": 2002}]}),
        encoding="utf-8",
    )
    return config


def test_films_command_links_local_files_and_keeps_inventory_on_missing_ones(
    monkeypatch, tmp_path, clean_env
):
    data = tmp_path / "data"
    layout = Layout(data)
    layout.results.mkdir(parents=True)
    layout.inventory.write_text(json.dumps({"items": []}), encoding="utf-8")
    truth = _ground_truth(tmp_path / "csv")
    alpha, beta = tmp_path / "alpha.mkv", tmp_path / "beta.mkv"
    alpha.write_bytes(b"a")
    beta.write_bytes(b"b")
    monkeypatch.setattr(films, "probe_film", lambda _path: (25.0, False))
    base = ["--data-root", str(data), "films", "--config", str(_films_config(tmp_path))]

    missing = folder_eval_cli.main(
        [*base, "--ground-truth-dir", str(truth), "--film", f"tt1={alpha}"]
    )
    assert missing == 2 and json.loads(layout.inventory.read_text())["items"] == []

    code = folder_eval_cli.main(
        [*base, "--ground-truth-dir", str(truth), "--film", f"tt1={alpha}",
         "--film", f"Beta (2002)={beta}"]
    )
    assert code == 0
    assert (data / "films" / "Alpha (2001)" / "alpha.mkv").resolve() == alpha.resolve()
    items = json.loads(layout.inventory.read_text())["items"]
    assert {item["imdb_id"] for item in items} == {"tt1", "tt2"}
    assert next(item for item in items if item["window"] == [10.0, 20.0])["expected"]
    copied = (data / "films" / "ground_truth" / "tag_timestamps.csv").read_text()
    assert "tt9" not in copied

    # A rerun without --ground-truth-dir reuses the copy left in the data root, and a
    # relative film path still yields a working link.
    monkeypatch.chdir(tmp_path)
    assert folder_eval_cli.main([*base, "--film", "tt1=alpha.mkv", "--film", f"tt2={beta}"]) == 0
    assert (data / "films" / "Alpha (2001)" / "alpha.mkv").read_bytes() == b"a"


@pytest.mark.parametrize(
    ("cli_key", "config_key"), [("Alpha (2001)", "tt1"), ("tt1", "Alpha (2001)")]
)
def test_cli_film_beats_the_local_config_whatever_the_key_form(
    tmp_path, clean_env, cli_key, config_key
):
    truth = _ground_truth(tmp_path / "csv")
    cli_file, config_file = tmp_path / "cli.mkv", tmp_path / "config.mkv"
    for path in (cli_file, config_file):
        path.write_bytes(b"x")
    config = tmp_path / "local.json"
    config.write_text(json.dumps({"films": {config_key: str(config_file)}}), encoding="utf-8")

    sources = resolve_sources(
        Layout(tmp_path / "data"),
        ground_truth_dir=truth,
        film_files=parse_film_options([f"{cli_key}={cli_file}"]),
        local_config=config,
    )
    assert sources.file_for("tt1", "Alpha (2001)") == cli_file.resolve()
    assert resolve_sources(
        Layout(tmp_path / "data"), ground_truth_dir=truth, local_config=config
    ).file_for("tt1", "Alpha (2001)") == config_file.resolve()


def test_environment_selects_the_local_config(monkeypatch, tmp_path, clean_env):
    truth = _ground_truth(tmp_path / "csv")
    config = tmp_path / "env.json"
    config.write_text(
        json.dumps({"ground_truth_dir": str(truth), "films": {"tt1": "a.mkv"}}), encoding="utf-8"
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "films.local.json").write_text(
        json.dumps({"films": {"tt1": "ignored.mkv"}}), encoding="utf-8"
    )
    monkeypatch.setenv(LOCAL_CONFIG_ENV, str(config))
    sources = resolve_sources(Layout(tmp_path / "data"))
    assert sources.ground_truth_dir == truth and sources.config_files == {"tt1": tmp_path / "a.mkv"}


@pytest.mark.parametrize("option", ["tt404", "Gamma (2003)"])
def test_unknown_film_keys_fail_before_the_inventory_changes(
    monkeypatch, tmp_path, clean_env, option
):
    data = tmp_path / "data"
    layout = Layout(data)
    layout.results.mkdir(parents=True)
    layout.inventory.write_text(json.dumps({"items": []}), encoding="utf-8")
    truth = _ground_truth(tmp_path / "csv")
    film = tmp_path / "film.mkv"
    film.write_bytes(b"x")
    monkeypatch.setattr(films, "probe_film", lambda _path: (25.0, False))

    sources = resolve_sources(
        layout,
        ground_truth_dir=truth,
        film_files=parse_film_options([f"tt1={film}", f"tt2={film}", f"{option}={film}"]),
    )
    with pytest.raises(FilmSourceError, match=f"not in films.json: {re.escape(option)}"):
        films.build_films(layout, sources, _films_config(tmp_path))
    assert json.loads(layout.inventory.read_text())["items"] == []
