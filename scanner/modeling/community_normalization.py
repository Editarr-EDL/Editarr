"""Community title/label normalization, alias resolution and label equality (§3.3–3.5)."""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

THEATRICAL = "theatrical"
_DELETED = str.maketrans("", "", "'\u2019\u02bc`")
_SPACES = re.compile(" +")
_ALIAS_ID = re.compile(r"[a-z0-9_]{1,64}")


def normalize(value: str) -> str:
    """Normalize a title, alias spelling or Other text exactly as §3.3 specifies."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    lowered = stripped.lower().translate(_DELETED)
    spaced = "".join(ch if unicodedata.category(ch)[0] in "LN" else " " for ch in lowered)
    return unicodedata.normalize("NFC", _SPACES.sub(" ", spaced).strip(" "))


def title_year_key(title: str, year: int | None) -> str:
    """Return the stored `title_year` key value: `normalize(title) + NUL + year`."""
    return f"{normalize(title)}\0{'' if year is None else year}"


@dataclass(frozen=True)
class Alias:
    """An approved alias with its normalized spellings."""

    alias_id: str
    label: str
    spellings: tuple[str, ...]


SEEDED_ALIASES: tuple[Alias, ...] = tuple(
    Alias(alias_id, label, tuple(normalize(spelling) for spelling in spellings))
    for alias_id, label, spellings in (
        (
            THEATRICAL,
            "Theatrical",
            (
                "",
                "Theatrical",
                "Theatrical Cut",
                "Theatrical Version",
                "Theatrical Edition",
                "Theatrical Release",
                "Original Theatrical",
                "Original Theatrical Cut",
            ),
        ),
        (
            "directors_cut",
            "Director's Cut",
            ("Director's Cut", "Directors Cut", "Director's Edition", "Director's Version"),
        ),
        (
            "extended",
            "Extended Edition",
            ("Extended Edition", "Extended", "Extended Cut", "Extended Version"),
        ),
        ("unrated", "Unrated", ("Unrated", "Unrated Cut", "Unrated Edition", "Unrated Version")),
    )
)


class AliasListError(ValueError):
    """An alias list is malformed or has a spelling claimed by two aliases."""


def alias_spelling_index(aliases: Iterable[Alias]) -> dict[str, str]:
    """Map each normalized spelling to its alias ID; the empty spelling is always Theatrical."""
    index: dict[str, str] = {"": THEATRICAL}
    for alias in aliases:
        if not _ALIAS_ID.fullmatch(alias.alias_id):
            raise AliasListError("Invalid alias ID")
        for spelling in alias.spellings:
            key = normalize(spelling)
            if index.get(key, alias.alias_id) != alias.alias_id:
                raise AliasListError("A normalized spelling belongs to two aliases")
            index[key] = alias.alias_id
    return index


def parse_alias_list(value: object) -> tuple[Alias, ...]:
    """Strictly parse a `GET /v1/aliases` body into aliases with normalized spellings."""
    if not isinstance(value, Mapping) or set(value) != {"version", "aliases"}:
        raise AliasListError("Invalid alias list")
    if value["version"] != 1 or type(value["version"]) is not int:
        raise AliasListError("Unsupported alias list version")
    items = value["aliases"]
    if not isinstance(items, list):
        raise AliasListError("Invalid alias list")
    aliases: list[Alias] = []
    for item in items:
        if not isinstance(item, Mapping) or set(item) != {"aliasId", "label", "spellings"}:
            raise AliasListError("Invalid alias")
        alias_id, label, spellings = item["aliasId"], item["label"], item["spellings"]
        if (
            not isinstance(alias_id, str)
            or not isinstance(label, str)
            or not label
            or not isinstance(spellings, list)
            or not all(isinstance(spelling, str) for spelling in spellings)
        ):
            raise AliasListError("Invalid alias")
        aliases.append(Alias(alias_id, label, tuple(normalize(s) for s in spellings)))
    if len({alias.alias_id for alias in aliases}) != len(aliases):
        raise AliasListError("Duplicate alias ID")
    if not aliases or aliases[0].alias_id != THEATRICAL:
        raise AliasListError("Theatrical must be the first alias")
    if not {alias.alias_id for alias in SEEDED_ALIASES} <= {alias.alias_id for alias in aliases}:
        raise AliasListError("A seeded alias is missing")
    labels = [alias.label.encode("utf-8") for alias in aliases[1:]]
    if labels != sorted(labels):
        raise AliasListError("Aliases after Theatrical must be ordered by display label")
    alias_spelling_index(aliases)
    return tuple(aliases)


@dataclass(frozen=True)
class ResolvedLabel:
    """A label as stored by the service: an alias ID, normalized free text, or both null."""

    alias_id: str | None
    label_text: str | None


def resolve_alias_choice(alias_id: str) -> ResolvedLabel:
    """Resolve an explicit alias choice (`{aliasId}`); approval is checked by the service."""
    return ResolvedLabel(alias_id, None)


def resolve_other_text(text: str | None, aliases: Iterable[Alias]) -> ResolvedLabel:
    """Resolve Other/`{text}` labels: normalized text, plus `aliasId` for approved spellings.

    Blank text is "no version" and therefore Theatrical with a null `labelText`.
    """
    normalized = normalize(text) if text is not None else ""
    if not normalized:
        return ResolvedLabel(THEATRICAL, None)
    return ResolvedLabel(alias_spelling_index(aliases).get(normalized), normalized)


def resolve_query_label(
    label: Mapping[str, object] | None, aliases: Iterable[Alias]
) -> ResolvedLabel:
    """Resolve a `/v1/match` label (`{aliasId}`, `{text}` or null, which means Theatrical)."""
    if label is None:
        return ResolvedLabel(THEATRICAL, None)
    if set(label) == {"aliasId"} and isinstance(label["aliasId"], str):
        return resolve_alias_choice(label["aliasId"])
    if set(label) == {"text"} and (label["text"] is None or isinstance(label["text"], str)):
        return resolve_other_text(label["text"], aliases)
    raise ValueError("A label is {aliasId}, {text} or null")


def _effective_alias(label: ResolvedLabel) -> str | None:
    if label.alias_id is not None:
        return label.alias_id
    return THEATRICAL if label.label_text is None else None


def labels_equal(left: ResolvedLabel, right: ResolvedLabel) -> bool:
    """Compare labels per §3.5: aliases match (null ≡ Theatrical), else free text matches.

    A null `aliasId` counts as Theatrical only when `labelText` is also null; free text
    with no alias never equals an aliased label.
    """
    left_alias, right_alias = _effective_alias(left), _effective_alias(right)
    if left_alias is not None or right_alias is not None:
        return left_alias == right_alias
    return left.label_text == right.label_text
