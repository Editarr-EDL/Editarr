"""Ground-truth labels inferred from folder structure via `label_rules.json`."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from scanner.modeling.model_core import MCF_CATEGORY_REGISTRY

DEFAULT_RULES_PATH = Path(__file__).with_name("label_rules.json")

FAMILY_TOKENS: dict[str, list[str]] = {
    family.value: [token for token, _label in entries[1:]]
    for family, entries in MCF_CATEGORY_REGISTRY.items()
}
# Each family's first registry entry is its general token (e.g. `violence`).
_GENERAL_TOKENS = {
    entries[0][0]: family.value for family, entries in MCF_CATEGORY_REGISTRY.items()
}


def family_of(key: str) -> str:
    return key.split("/", 1)[0]


def is_valid_key(key: str) -> bool:
    family, _, token = key.partition("/")
    if family not in FAMILY_TOKENS:
        return False
    return not token or token in FAMILY_TOKENS[family]


def with_families(keys: list[str] | set[str]) -> list[str]:
    """Return keys plus the family implied by every `family/token` key, sorted."""
    result = set(keys)
    result.update(family_of(key) for key in keys)
    return sorted(result)


def _segment_index() -> dict[str, str]:
    """Map casefolded family names and tokens to their canonical keys."""
    index: dict[str, str] = {}
    for family, tokens in FAMILY_TOKENS.items():
        for token in tokens:
            index[token.casefold()] = f"{family}/{token}"
    for token, family in _GENERAL_TOKENS.items():
        index[token.casefold()] = family
    for family in FAMILY_TOKENS:
        index[family.casefold()] = family
    return index


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    expected: tuple[str, ...] = ()
    scored: bool = True
    codes: dict[str, tuple[str, ...]] | None = None
    ignore_codes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Label:
    expected: list[str]
    scored: bool
    rule: str | None
    notes: list[str] = field(default_factory=list)


def _validated_keys(keys: Any, where: str) -> tuple[str, ...]:
    if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
        raise ValueError(f"{where}: expected keys must be a list of strings")
    for key in keys:
        if not is_valid_key(key):
            raise ValueError(f"{where}: unknown MCF key {key!r}")
    return tuple(keys)


class LabelRules:
    """Ordered regex rules plus path-segment token/family/synonym inference."""

    def __init__(self, rules: list[Rule], synonyms: dict[str, tuple[str, ...]]) -> None:
        self.rules = rules
        self.segments = _segment_index()
        self.synonyms = {name.casefold(): keys for name, keys in synonyms.items()}

    @classmethod
    def load(cls, path: Path = DEFAULT_RULES_PATH) -> LabelRules:
        payload = json.loads(path.read_text(encoding="utf-8"))
        synonyms = {
            name: _validated_keys(keys, f"synonym {name!r}")
            for name, keys in payload.get("synonyms", {}).items()
        }
        rules = []
        for index, raw in enumerate(payload.get("rules", []), start=1):
            name = raw.get("name") or f"rule-{index}"
            codes = None
            if "codes" in raw:
                codes = {
                    code: _validated_keys(keys, f"{name} code {code!r}")
                    for code, keys in raw["codes"].items()
                }
            rules.append(
                Rule(
                    name=name,
                    pattern=re.compile(raw["pattern"]),
                    expected=_validated_keys(raw.get("expected", []), name),
                    scored=bool(raw.get("scored", True)),
                    codes=codes,
                    ignore_codes=frozenset(raw.get("ignore_codes", [])),
                )
            )
        return cls(rules, synonyms)

    def segment_keys(self, relative_path: str) -> set[str]:
        """Keys implied by directory names (not the file name) in the path."""
        keys: set[str] = set()
        for segment in PurePosixPath(relative_path).parts[:-1]:
            folded = segment.casefold()
            keys.update(self.synonyms.get(folded, ()))
            if folded in self.segments:
                keys.add(self.segments[folded])
        return keys

    def label(self, relative_path: str) -> Label:
        """Return expected keys (families implied) and whether the item is scored."""
        keys = self.segment_keys(relative_path)
        notes: list[str] = []
        for rule in self.rules:
            match = rule.pattern.search(relative_path)
            if not match:
                continue
            keys.update(rule.expected)
            scored = rule.scored
            if rule.codes is not None:
                raw_codes = match.groupdict().get("codes") or ""
                for code in filter(None, raw_codes.split("-")):
                    if code in rule.ignore_codes:
                        continue
                    if code not in rule.codes:
                        notes.append(f"unknown code {code!r}")
                        scored = False
                        continue
                    keys.update(rule.codes[code])
            return Label(with_families(keys), scored, rule.name, notes)
        if keys:
            return Label(with_families(keys), True, None, notes)
        return Label([], False, None, ["no rule matched"])
