"""Host path resolution reads a server path with the separator rules placement uses.

Drive (`C:\\`, `C:/`) and UNC paths split on `\\` and `/`. A `/`-rooted path splits
only on `/`, so a `\\` in a POSIX name stays a literal character on the host.
"""

from __future__ import annotations

import ntpath
from pathlib import Path

import pytest

from scanner import repository
from scanner.integrations.jellyfin_paths import library_of
from scanner.models import JellyfinLibrary, PathMappingCreate
from scanner.persistence.repository import repository_paths
from tests.editarr_support import db as db


def _map(db, remote: str, local: Path):
    return repository.create_path_mapping(
        db, PathMappingCreate(remote_path=remote, local_path=str(local))
    )


@pytest.mark.parametrize(
    ("remote", "path", "suffix"),
    [
        (r"Z:\Media\Movies", r"Z:\Media\Movies\Example\movie.mkv", ("Example", "movie.mkv")),
        (r"Z:\Media\Movies", "Z:/Media/Movies/Example/movie.mkv", ("Example", "movie.mkv")),
        ("Z:/Media/Movies", r"Z:/Media\Movies\Example/movie.mkv", ("Example", "movie.mkv")),
        ("Z:\\", r"Z:\Example\movie.mkv", ("Example", "movie.mkv")),
        (r"\\nas\share\Movies", r"\\nas\share\Movies\Example\movie.mkv", ("Example", "movie.mkv")),
        (r"\\nas\share\Movies", "//nas/share/Movies/Example/movie.mkv", ("Example", "movie.mkv")),
        ("//nas/share", r"\\nas\share\Movies\movie.mkv", ("Movies", "movie.mkv")),
    ],
)
def test_drive_and_unc_paths_map_on_either_separator(db, tmp_path, remote, path, suffix):
    mapping = _map(db, remote, tmp_path)

    resolution = repository.resolve_local_path(db, path)

    assert resolution.mapping_id == mapping.id
    assert resolution.local_path == str(tmp_path.joinpath(*suffix))


def test_drive_and_unc_mappings_store_slash_spellings(db, tmp_path):
    assert _map(db, r"Z:\Media\Movies\ ", tmp_path).remote_path == "Z:/Media/Movies"
    assert _map(db, "Y:\\", tmp_path).remote_path == "Y:"
    assert _map(db, r"\\nas\share\Movies", tmp_path).remote_path == "//nas/share/Movies"


def test_an_unmapped_drive_path_keeps_its_slash_spelling(db):
    resolution = repository.resolve_local_path(db, r"Z:\Media\movie.mkv")

    assert (resolution.mapping_id, resolution.local_path) == (None, "Z:/Media/movie.mkv")


def test_a_backslash_in_a_mapped_posix_name_stays_one_host_name(db, tmp_path):
    mapping = _map(db, "/remote/media", tmp_path)

    resolution = repository.resolve_local_path(db, "/remote/media/x\\..\\..\\b/movie.mkv")

    assert resolution.mapping_id == mapping.id
    assert resolution.local_path == str(tmp_path / "x\\..\\..\\b" / "movie.mkv")
    assert Path(resolution.local_path).resolve().parent.parent == tmp_path.resolve()


def test_an_unmapped_posix_path_keeps_its_backslashes(db):
    path = "/remote/media/x\\..\\..\\b/movie.mkv"

    assert repository.resolve_local_path(db, path).local_path == path


def test_a_posix_name_with_a_backslash_is_not_under_its_prefix(db, tmp_path):
    _map(db, "/remote/media", tmp_path)

    resolution = repository.resolve_local_path(db, "/remote/media\\other/movie.mkv")

    assert resolution.mapping_id is None
    assert resolution.local_path == "/remote/media\\other/movie.mkv"


def test_a_posix_mapping_keeps_backslashes_in_its_names(db, tmp_path):
    mapping = _map(db, "/remote\\media", tmp_path)

    assert mapping.remote_path == "/remote\\media"
    assert repository.resolve_local_path(db, "/remote/media/movie.mkv").mapping_id is None
    assert repository.resolve_local_path(db, "/remote\\media/movie.mkv").mapping_id == mapping.id


@pytest.mark.parametrize(
    "path",
    [
        "relative/movie.mkv",
        "C:Media\\movie.mkv",
        "\\Media\\movie.mkv",
        "/\\nas\\share\\movie.mkv",
        "\\/nas/share/movie.mkv",
        "//nas",
    ],
)
def test_a_path_without_an_established_form_is_refused(db, tmp_path, path):
    with pytest.raises(ValueError, match="absolute POSIX or Windows path"):
        repository.resolve_local_path(db, path)
    with pytest.raises(ValueError, match="absolute POSIX or Windows path"):
        _map(db, path, tmp_path)


def test_a_windows_host_refuses_a_posix_name_it_would_split(db, tmp_path, monkeypatch):
    _map(db, "/remote/media", tmp_path)
    monkeypatch.setattr(repository_paths, "_HOST_PATHS", ntpath)

    with pytest.raises(ValueError, match="cannot name"):
        repository.resolve_local_path(db, "/remote/media/x\\..\\b/movie.mkv")
    with pytest.raises(ValueError, match="cannot name"):
        repository.resolve_local_path(db, "/remote/other\\name.mkv")
    assert repository.resolve_local_path(db, r"Z:\Media\movie.mkv").mapping_id is None


LOCATIONS = {
    "a": "/media/a",
    "b": "/media/b",
    "c": "C:\\Media\\c",
    "d": "\\\\nas\\share\\d",
}


@pytest.mark.parametrize(
    ("path", "placed"),
    [
        ("/media/a/movie.mkv", "a"),
        ("/media/a/x\\..\\..\\b/movie.mkv", "a"),
        ("/media/a/x\\y/movie.mkv", "a"),
        ("/media/a\\b/movie.mkv", None),
        ("/media/a\\..\\b/movie.mkv", None),
        ("/media/b/movie.mkv", "b"),
        ("C:\\Media\\c\\movie.mkv", "c"),
        ("C:/Media/c/movie.mkv", "c"),
        ("C:/Media\\c/x/movie.mkv", "c"),
        ("C:\\Media\\cc\\movie.mkv", None),
        ("\\\\nas\\share\\d\\movie.mkv", "d"),
        ("//nas/share/d/movie.mkv", "d"),
        ("\\\\nas/share\\d/movie.mkv", "d"),
        ("\\media\\a\\movie.mkv", None),
        ("/\\media\\a\\movie.mkv", None),
        ("/media/other/movie.mkv", None),
    ],
)
def test_placement_and_resolution_read_a_spelling_the_same_way(db, tmp_path, path, placed):
    libraries = [
        JellyfinLibrary(id=key, name=key, locations=[loc]) for key, loc in LOCATIONS.items()
    ]
    mapped = {_map(db, location, tmp_path / key).id: key for key, location in LOCATIONS.items()}

    try:
        resolution = repository.resolve_local_path(db, path)
    except ValueError:
        resolved_library = None
    else:
        resolved_library = mapped.get(resolution.mapping_id)
        if resolved_library is not None:
            local_root = (tmp_path / resolved_library).resolve()
            assert Path(resolution.local_path).resolve().is_relative_to(local_root)

    assert library_of(path, libraries) == placed
    assert resolved_library == placed
