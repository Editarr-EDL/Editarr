import pytest

from scanner.http.catalog_library_order import library_jump_groups, library_sort_value
from scanner.models import JellyfinMediaItem

ITEMS = [
    JellyfinMediaItem(
        id="a",
        item_type="Movie",
        name="Arrival",
        year=2016,
        official_rating="PG-13",
        duration_seconds=116 * 60,
    ),
    JellyfinMediaItem(
        id="b",
        item_type="Series",
        name="Blue",
        year=2009,
        official_rating="TV-MA",
        duration_seconds=45 * 60,
    ),
    JellyfinMediaItem(
        id="c",
        item_type="Movie",
        name="Comet",
        year=2019,
        official_rating="pg-13",
        duration_seconds=90 * 60,
    ),
    JellyfinMediaItem(
        id="d",
        item_type="MusicVideo",
        name="Dune",
        year=None,
        official_rating=None,
        duration_seconds=None,
    ),
    JellyfinMediaItem(
        id="e",
        item_type="movie",
        name="Echo",
        year=2020,
        official_rating="G",
        duration_seconds=150 * 60,
    ),
    JellyfinMediaItem(
        id="f",
        item_type="Movie",
        name="Falcon",
        year=2010,
        official_rating="PG",
        duration_seconds=181 * 60,
    ),
    JellyfinMediaItem(
        id="g",
        item_type="Movie",
        name="Glass",
        year=1999,
        official_rating=None,
        duration_seconds=149 * 60 + 59,
    ),
]


def _groups(sort_by: str, direction: str) -> list[tuple[str, str, int]]:
    ordered = sorted(
        ITEMS, key=lambda item: library_sort_value(item, sort_by), reverse=direction == "desc"
    )
    return [
        (group.key, group.label, group.offset) for group in library_jump_groups(ordered, sort_by)
    ]


@pytest.mark.parametrize(
    ("sort_by", "ascending"),
    [
        (
            "title",
            [
                ("A", "A", 0),
                ("B", "B", 1),
                ("C", "C", 2),
                ("D", "D", 3),
                ("E", "E", 4),
                ("F", "F", 5),
                ("G", "G", 6),
            ],
        ),
        (
            "year",
            [
                ("1990", "1990s", 0),
                ("2000", "2000s", 1),
                ("2010", "2010s", 2),
                ("2020", "2020s", 5),
                ("none", "No year", 6),
            ],
        ),
        # Ratings group by folded value, labelled as first listed; unrated items sort last.
        (
            "rating",
            [
                ("g", "G", 0),
                ("pg", "PG", 1),
                ("pg-13", "PG-13", 2),
                ("tv-ma", "TV-MA", 4),
                ("none", "No rating", 5),
            ],
        ),
        (
            "duration",
            [
                ("0", "<90m", 0),
                ("90", "90m", 1),
                ("120", "2h", 3),
                ("150", "2h30", 4),
                ("180", "3h+", 5),
                ("none", "No length", 6),
            ],
        ),
        (
            "type",
            [("movie", "Movie", 0), ("musicvideo", "Music video", 5), ("series", "Series", 6)],
        ),
    ],
)
def test_jump_groups_follow_each_sort_in_both_directions(sort_by, ascending):
    assert _groups(sort_by, "asc") == ascending
    sizes = [
        (following[2] if following else len(ITEMS)) - group[2]
        for group, following in zip(ascending, [*ascending[1:], None], strict=True)
    ]
    descending, offset = [], 0
    for (key, label, _), size in reversed(list(zip(ascending, sizes, strict=True))):
        descending.append((key, offset, size))
        offset += size
    assert [(key, offset) for key, _label, offset in _groups(sort_by, "desc")] == [
        (key, offset) for key, offset, _size in descending
    ]


def test_jump_groups_of_an_empty_listing_are_empty():
    assert library_jump_groups([], "year") == []
