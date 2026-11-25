"""Pure master snapshot values for community master filter sets (local API §3, §8).

A link always follows its latest verified snapshot (ADR 0002 U3): filters are matched
to the local rows by `filterId`, and a served filter whose `mergedIds` names a row
that is no longer served continues that row (`rekeys`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

VALUE_FIELDS = ("startMs", "endMs", "subcategory", "severity", "action")
INFO_FIELDS = ("confidence", "contributorCount", "support", "negative", "spread")


@dataclass(frozen=True)
class MasterValues:
    """The material master values of one filter, in snapshot (camelCase) terms."""

    start_ms: int
    end_ms: int
    subcategory: str
    severity: str
    action: str
    low_confidence: bool = False
    present: bool = True

    def as_json(self) -> dict:
        """The values in snapshot (camelCase) terms."""
        return {
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "subcategory": self.subcategory,
            "severity": self.severity,
            "action": self.action,
            "lowConfidence": self.low_confidence,
            "present": self.present,
        }

    @classmethod
    def from_json(cls, document: Mapping) -> MasterValues:
        return cls(
            start_ms=int(document["startMs"]),
            end_ms=int(document["endMs"]),
            subcategory=str(document["subcategory"]),
            severity=str(document["severity"]),
            action=str(document["action"]),
            low_confidence=bool(document.get("lowConfidence", False)),
            present=bool(document.get("present", True)),
        )


@dataclass(frozen=True)
class MasterFilter:
    """One served filter of a verified snapshot."""

    filter_id: str
    category: str
    values: MasterValues
    info: Mapping = field(default_factory=dict)
    merged_ids: tuple[str, ...] = ()


def master_filters(document: Mapping) -> tuple[MasterFilter, ...]:
    """Read the filters of an already verified, strictly parsed snapshot document."""
    return tuple(
        MasterFilter(
            filter_id=item["filterId"],
            category=item["category"],
            values=MasterValues.from_json(item),
            info={name: item[name] for name in INFO_FIELDS if name in item},
            merged_ids=tuple(item.get("mergedIds") or ()),
        )
        for item in document["filters"]
    )


def rekeys(row_ids: Iterable[str], snapshot: Iterable[MasterFilter]) -> dict[str, str]:
    """Map local row IDs to the merged filter ID each now carries.

    A row with the filter's own ID continues it. Otherwise the lowest row ID among the
    rows a served filter names in `mergedIds` re-keys to it; the others stay behind,
    no longer served.
    """
    snapshot = tuple(snapshot)
    rows = set(row_ids)
    served = {item.filter_id for item in snapshot}
    found: dict[str, str] = {}
    for item in snapshot:
        if item.filter_id in rows:
            continue
        candidates = sorted(old for old in item.merged_ids if old in rows and old not in served)
        candidates = [old for old in candidates if old not in found]
        if candidates:
            found[candidates[0]] = item.filter_id
    return found
