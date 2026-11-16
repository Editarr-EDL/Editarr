"""Jellyfin server paths are placed by lexical containment in Jellyfin's own namespace.

A `/`-rooted path splits only on `/`; drive and UNC paths split on `\\` and `/`.
Any `.` or `..` segment, in the path or a location, denies placement.
"""

from __future__ import annotations

import pytest

from scanner.integrations.jellyfin_paths import library_of
from scanner.models import JellyfinLibrary


def _place(path: str, location: str) -> str | None:
    return library_of(path, [JellyfinLibrary(id="a", name="A", locations=[location])])


@pytest.mark.parametrize(
    ("location", "path", "expected"),
    [
        ("/media/Movies/", "/media/Movies/Film.mkv", "a"),
        ("/media/Movies", "/media/Movies//Film.mkv", "a"),
        ("/media/Movies", "/media/Movies/Film.mkv/", "a"),
        ("/media/Movies", "/media/Movies2/Film.mkv", None),
        ("/media/Movies2", "/media/Movies/Film.mkv", None),
        ("/media/Movies", "/media/movies/Film.mkv", None),
        ("/", "/media/Movies/Film.mkv", "a"),
        ("/media/Movies", "/media/Movies/x\\y/Film.mkv", "a"),
        ("/media/Movies", "/media/Movies\\Film.mkv", None),
        ("/media\\Movies", "/media/Movies/Film.mkv", None),
        ("/media\\Movies", "/media\\Movies/Film.mkv", "a"),
    ],
)
def test_posix_paths(location, path, expected):
    assert _place(path, location) == expected


@pytest.mark.parametrize(
    ("location", "path", "expected"),
    [
        ("C:\\Media\\Movies\\", "C:/Media/Movies/Film.mkv", "a"),
        ("C:\\Media\\Movies", "C:/Media/Movies\\Film.mkv", "a"),
        ("C:\\Media\\Movies", "C:\\Media\\Movies2\\Film.mkv", None),
        ("C:\\Media\\Movies", "D:\\Media\\Movies\\Film.mkv", None),
        ("C:\\Media\\Movies", "/Media/Movies/Film.mkv", None),
        ("C:\\Media\\Movies", "C:Media\\Movies\\Film.mkv", None),
        ("C:/Media/Movies", "c:/media/movies/Film.mkv", None),
        ("\\\\nas\\share\\Movies", "\\\\nas\\share\\Movies\\Film.mkv", "a"),
        ("\\\\nas\\share\\Movies", "//nas/share/Movies/Film.mkv", "a"),
        ("\\\\nas\\share", "\\\\nas\\other\\Film.mkv", None),
        ("\\\\nas\\share", "\\\\other\\share\\Film.mkv", None),
        ("//nas/share/Movies", "/nas/share/Movies/Film.mkv", None),
        ("/media/Movies", "\\media\\Movies\\Film.mkv", None),
        ("/nas/share", "/\\nas\\share\\Film.mkv", None),
    ],
)
def test_windows_drive_and_unc_paths(location, path, expected):
    assert _place(path, location) == expected


@pytest.mark.parametrize(
    ("location", "path"),
    [
        ("/media/Movies", "/media/./Movies/./Film.mkv"),
        ("/media/Movies", "/media/Other/../Movies/Film.mkv"),
        ("/media/Movies", "/media/Movies/Film.mkv/."),
        ("/media", "/../media/Film.mkv"),
        ("C:\\Media\\Movies", "C:\\Media\\Old\\..\\Movies\\Film.mkv"),
        ("C:\\Media\\Movies", "C:/Media/Movies/./Film.mkv"),
        ("\\\\nas\\share\\Movies", "\\\\nas\\share\\..\\share\\Movies\\Film.mkv"),
        ("\\\\nas\\share\\Movies", "\\\\nas\\..\\nas\\share\\Movies\\Film.mkv"),
        ("\\\\nas\\share", "\\\\nas\\.\\Film.mkv"),
        ("/media/Movies", "media/Movies/Film.mkv"),
        ("/media/Movies", "./media/Movies/Film.mkv"),
    ],
)
def test_a_path_with_dot_segments_or_without_a_root_has_no_library(location, path):
    assert _place(path, location) is None


@pytest.mark.parametrize(
    "location",
    ["/media/old/../Movies", "/media/./Movies", "/../media/Movies", "C:\\Media\\..\\Media\\Movies"],
)
def test_a_location_with_dot_segments_holds_nothing(location):
    assert _place("/media/Movies/Film.mkv", location) is None
    assert _place("C:\\Media\\Movies\\Film.mkv", location) is None


def test_a_backslash_in_a_posix_name_does_not_climb_into_another_library():
    a = JellyfinLibrary(id="a", name="A", locations=["/media/a"])
    b = JellyfinLibrary(id="b", name="B", locations=["/media/b"])
    crafted = "/media/a/x\\..\\..\\b/film.mkv"

    assert library_of(crafted, [a, b]) == "a"
    assert library_of(crafted, [b]) is None


def test_a_file_under_two_libraries_has_no_library():
    a = JellyfinLibrary(id="a", name="A", locations=["/media"])
    b = JellyfinLibrary(id="b", name="B", locations=["/media/Movies"])
    same = JellyfinLibrary(id="a", name="A", locations=["/media", "/media/Movies"])

    assert library_of("/media/Movies/Film.mkv", [a, b]) is None
    assert library_of("/media/Other/Film.mkv", [a, b]) == "a"
    assert library_of("/media/Movies/Film.mkv", [same]) == "a"
