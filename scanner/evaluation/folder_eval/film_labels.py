"""Window-level ground truth for films from timestamped content tags."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .labels import is_valid_key, with_families

DEFAULT_TAG_MAP_PATH = Path(__file__).with_name("film_tag_map.json")


@dataclass(frozen=True)
class Tag:
    name: str  # "category/tag_name"
    start: float
    end: float


@dataclass(frozen=True)
class WindowLabel:
    expected: list[str]
    scored: bool
    source_tags: list[str]


class TagMap:
    """Editable mapping from film tags to MCF keys (see `film_tag_map.json`)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.padding = float(payload.get("padding_seconds", 3))
        self.positive: dict[str, tuple[str, ...]] = {}
        for tag, keys in payload.get("positive", {}).items():
            invalid = [key for key in keys if not is_valid_key(key)]
            if invalid:
                raise ValueError(f"film tag {tag!r}: unknown MCF keys {invalid}")
            self.positive[tag] = tuple(keys)
        self.ambiguous = frozenset(payload.get("ambiguous", []))
        self.ignored_categories = frozenset(payload.get("ignored_categories", []))

    @classmethod
    def load(cls, path: Path = DEFAULT_TAG_MAP_PATH) -> TagMap:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def unmapped(self, tag_names: set[str]) -> set[str]:
        """Tags that are neither mapped, ambiguous, nor in an ignored category."""
        return {
            name for name in tag_names
            if name not in self.positive
            and name not in self.ambiguous
            and name.split("/", 1)[0] not in self.ignored_categories
        }

    def label_window(self, start: float, end: float, tags: list[Tag]) -> WindowLabel:
        """Label one [start, end) window from the tags whose padded interval intersects it."""
        overlapping = [
            tag for tag in tags
            if tag.start - self.padding < end and tag.end + self.padding >= start
        ]
        names = {tag.name for tag in overlapping}
        keys: set[str] = set()
        for name in names:
            keys.update(self.positive.get(name, ()))
        scored = bool(keys) or not (names & self.ambiguous)
        return WindowLabel(with_families(keys), scored, sorted(names))
