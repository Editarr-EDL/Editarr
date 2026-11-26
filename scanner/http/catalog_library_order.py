"""Library listing order and the jump groups that divide an ordered listing."""

import unicodedata
from collections.abc import Callable

from scanner.models import JellyfinMediaItem, LibraryJumpGroup

# Key of the group of items without a value for the sort (no year, rating, or length).
# Rating keys starting with ":" are escaped with another, so no rating can take this key.
MISSING_GROUP_KEY = ":none"
# Lower bounds, in minutes, of the length bands and their labels.
_LENGTH_BANDS = ((180, "3h+"), (150, "2h30"), (120, "2h"), (90, "90m"), (0, "<90m"))
_TYPE_LABELS = {"MusicVideo": "Music video"}


def normalized_title(value: str) -> str:
    """Fold a title for matching and ordering: accents removed, case folded, dotless ı read as i."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value).casefold().replace("ı", "i")
        if not unicodedata.combining(character)
    )


def title_letter(item: JellyfinMediaItem) -> str:
    """Return the A–Z initial a title sorts under, or "#" for anything else.

    Accents are removed and case folded first, so "Élan" is "E" and "ß" is "S" ("ss").
    """
    return _initial(normalized_title(item.sort_name or item.name))


def _initial(folded: str) -> str:
    initial = folded[:1].upper()
    return initial if "A" <= initial <= "Z" else "#"


def library_sort_value(item: JellyfinMediaItem, sort_by: str) -> tuple[object, ...]:
    """Produce a stable sort key for a library item and requested ordering.

    Each key starts with the item's jump group, so every group is contiguous in either direction.
    """
    if sort_by == "rating":
        return (
            item.official_rating is None,
            normalized_title(item.official_rating or ""),
            normalized_title(item.name),
            item.id,
        )
    if sort_by == "duration":
        return (
            item.duration_seconds is None,
            item.duration_seconds or 0,
            normalized_title(item.name),
            item.id,
        )
    if sort_by == "year":
        return (item.year is None, item.year or 0, normalized_title(item.name), item.id)
    if sort_by == "type":
        return (item.item_type.casefold(), normalized_title(item.sort_name or item.name), item.id)
    # Titles outside A–Z (digits, symbols, other scripts) form one "#" group before A.
    folded = normalized_title(item.sort_name or item.name)
    return (_initial(folded) != "#", folded, item.id)


def _year_group(item: JellyfinMediaItem, _value: tuple[object, ...]) -> tuple[str, str]:
    if item.year is None:
        return MISSING_GROUP_KEY, "No year"
    decade = item.year // 10 * 10
    return str(decade), f"{decade}s"


def _rating_group(item: JellyfinMediaItem, value: tuple[object, ...]) -> tuple[str, str]:
    if item.official_rating is None:
        return MISSING_GROUP_KEY, "No rating"
    folded = str(value[1])
    return (f":{folded}" if folded.startswith(":") else folded), item.official_rating


def _length_group(item: JellyfinMediaItem, _value: tuple[object, ...]) -> tuple[str, str]:
    if item.duration_seconds is None:
        return MISSING_GROUP_KEY, "No length"
    minutes = item.duration_seconds / 60
    bound, label = next((band for band in _LENGTH_BANDS if minutes >= band[0]), _LENGTH_BANDS[-1])
    return str(bound), label


def _type_group(item: JellyfinMediaItem, value: tuple[object, ...]) -> tuple[str, str]:
    return str(value[0]), _TYPE_LABELS.get(item.item_type, item.item_type)


def _title_group(_item: JellyfinMediaItem, value: tuple[object, ...]) -> tuple[str, str]:
    # The sort value holds the folded title, so it isn't folded again.
    letter = _initial(str(value[1])) if value[0] else "#"
    return letter, letter


_GROUPS: dict[str, Callable[[JellyfinMediaItem, tuple[object, ...]], tuple[str, str]]] = {
    "title": _title_group,
    "year": _year_group,
    "rating": _rating_group,
    "duration": _length_group,
    "type": _type_group,
}


def sort_library(
    items: list[JellyfinMediaItem], sort_by: str, *, descending: bool
) -> tuple[list[JellyfinMediaItem], list[LibraryJumpGroup]]:
    """Sort a listing and divide it into jump groups, computing each item's sort value once."""
    keyed = sorted(
        ((library_sort_value(item, sort_by), item) for item in items),
        key=lambda pair: pair[0],
        reverse=descending,
    )
    return [item for _value, item in keyed], _jump_groups(keyed, sort_by)


def _jump_groups(
    keyed: list[tuple[tuple[object, ...], JellyfinMediaItem]], sort_by: str
) -> list[LibraryJumpGroup]:
    group_of = _GROUPS[sort_by]
    groups: list[LibraryJumpGroup] = []
    for index, (value, item) in enumerate(keyed):
        key, label = group_of(item, value)
        if not groups or groups[-1].key != key:
            groups.append(LibraryJumpGroup(key=key, label=label, offset=index))
    return groups


def library_jump_groups(items: list[JellyfinMediaItem], sort_by: str) -> list[LibraryJumpGroup]:
    """List the groups of an already sorted listing in order, each with the index of its first item.

    Titles group by initial, years by decade, ratings by value, lengths by band, and types by type;
    items missing the sorted value form one group where the sort places them.
    """
    return _jump_groups([(library_sort_value(item, sort_by), item) for item in items], sort_by)
