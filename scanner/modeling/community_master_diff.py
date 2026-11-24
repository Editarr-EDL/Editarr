"""Pure update review for community master filter sets (local API §9).

A filter row's baseline is the master values it last accepted. A new snapshot is
compared with those baselines by `filterId`, after merged IDs re-key their rows.
Only material fields need review; informational ones (confidence, counts, spread)
refresh silently. A dismissal hides a row until the master values change again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

NEW = "new"
CHANGED = "changed"
REMOVED = "removed"
LOW_CONFIDENCE = "low_confidence"
CHANGES = (NEW, CHANGED, REMOVED, LOW_CONFIDENCE)
# The API names each change's group; `low_confidence` is shown as `nowLowConfidence`.
GROUPS = {NEW: "new", CHANGED: "changed", REMOVED: "removed", LOW_CONFIDENCE: "nowLowConfidence"}

ACCEPT = "accept"
KEEP_MINE = "keep_mine"
DISMISS = "dismiss"

VALUE_FIELDS = ("startMs", "endMs", "subcategory", "severity", "action")
MATERIAL_FIELDS = (*VALUE_FIELDS, "lowConfidence", "present")
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
        """The values in the dismissal digest's field order."""
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

    def digest(self) -> str:
        """SHA-256 of the compact JSON of the material values: the dismissal digest."""
        encoded = json.dumps(self.as_json(), separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def differing(self, other: MasterValues) -> tuple[str, ...]:
        """Material field names whose values differ from `other`, in field order."""
        mine, theirs = self.as_json(), other.as_json()
        return tuple(name for name in MATERIAL_FIELDS if mine[name] != theirs[name])


@dataclass(frozen=True)
class MasterFilter:
    """One served filter of a verified snapshot."""

    filter_id: str
    category: str
    values: MasterValues
    info: Mapping = field(default_factory=dict)
    merged_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BaselineRow:
    """A local filter row as the diff needs it."""

    filter_id: str
    category: str
    values: MasterValues
    touched: bool = False


@dataclass(frozen=True)
class UpdateRow:
    """One undecided change. `new_values` is the master after the change; for a
    removed filter it is the baseline with `present` false."""

    filter_id: str
    change: str
    category: str
    changed_fields: tuple[str, ...]
    new_values: MasterValues
    touched: bool
    info: Mapping = field(default_factory=dict)
    merged_into: str | None = None

    @property
    def digest(self) -> str:
        return self.new_values.digest()


@dataclass(frozen=True)
class MasterDiff:
    """The result of comparing baselines with a snapshot.

    `rekeys` maps an old row ID to the merged filter ID it now carries. `info`
    holds the informational values to refresh at once for every existing row
    (keyed by the row's ID after re-keying). `rows` are the changes to review;
    `kept_dismissals` are the dismissals that still hide a row.
    """

    rekeys: Mapping[str, str]
    info: Mapping[str, Mapping]
    rows: tuple[UpdateRow, ...]
    kept_dismissals: frozenset[str]


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


def _rekeys(baseline: Mapping[str, BaselineRow], snapshot: Iterable[MasterFilter]):
    """Choose which old row each merged filter continues; return (rekeys, merged_away).

    A row with the filter's own ID continues it. Otherwise the touched row, or else
    the lowest ID, among the rows named in `mergedIds` re-keys. The others are removed
    with `mergedInto`.
    """
    served = {item.filter_id for item in snapshot}
    rekeys: dict[str, str] = {}
    merged_away: dict[str, str] = {}
    for item in snapshot:
        candidates = sorted(old for old in item.merged_ids if old in baseline and old not in served)
        if not candidates:
            continue
        if item.filter_id in baseline:
            chosen = None
        else:
            touched = [old for old in candidates if baseline[old].touched]
            chosen = (touched or candidates)[0]
            rekeys[chosen] = item.filter_id
        for old in candidates:
            if old != chosen:
                merged_away[old] = item.filter_id
    return rekeys, merged_away


def _info(item: MasterFilter) -> dict:
    return {name: item.info[name] for name in INFO_FIELDS if name in item.info}


def diff_master(
    baseline: Iterable[BaselineRow],
    snapshot: Iterable[MasterFilter],
    dismissed: Mapping[str, str] | None = None,
) -> MasterDiff:
    """Compare local baselines with a new snapshot; `dismissed` maps filter ID to digest.

    Groups follow §9: a filter not in the baseline is `new`; a lowConfidence flip to
    true that is the only material change is `low_confidence`; any other material
    difference, including presence, is `changed`; a baseline row the snapshot no
    longer serves is `removed`. Rows whose master was already absent stay quiet.
    """
    snapshot = tuple(snapshot)
    dismissed = dict(dismissed or {})
    rows_by_id = {row.filter_id: row for row in baseline}
    rekeys, merged_away = _rekeys(rows_by_id, snapshot)
    current = {
        rekeys.get(old, old): replace(row, filter_id=rekeys.get(old, old))
        for old, row in rows_by_id.items()
    }
    renamed_dismissals = {rekeys.get(old, old): digest for old, digest in dismissed.items()}
    updates: list[UpdateRow] = []
    info: dict[str, dict] = {}
    served = set()
    for item in snapshot:
        served.add(item.filter_id)
        values = replace(item.values, present=True)
        row = current.get(item.filter_id)
        if row is None:
            updates.append(
                UpdateRow(
                    item.filter_id, NEW, item.category, MATERIAL_FIELDS, values, False, _info(item)
                )
            )
            continue
        info[item.filter_id] = _info(item)
        fields = values.differing(row.values)
        if not fields:
            continue
        change = (
            LOW_CONFIDENCE if fields == ("lowConfidence",) and values.low_confidence else CHANGED
        )
        updates.append(
            UpdateRow(
                item.filter_id, change, row.category, fields, values, row.touched, _info(item)
            )
        )
    for filter_id, row in current.items():
        if filter_id in served or not row.values.present:
            continue
        gone = replace(row.values, present=False)
        updates.append(
            UpdateRow(
                filter_id,
                REMOVED,
                row.category,
                ("present",),
                gone,
                row.touched,
                merged_into=merged_away.get(filter_id),
            )
        )
    kept = frozenset(
        update.filter_id
        for update in updates
        if renamed_dismissals.get(update.filter_id) == update.digest
    )
    visible = tuple(
        sorted(
            (update for update in updates if update.filter_id not in kept),
            key=lambda update: (CHANGES.index(update.change), update.filter_id),
        )
    )
    return MasterDiff(rekeys=rekeys, info=info, rows=visible, kept_dismissals=kept)


def allowed_decisions(change: str, touched: bool) -> tuple[str, ...]:
    """The decisions a row offers: touched changed and removed rows add Keep mine."""
    if change not in CHANGES:
        raise ValueError(f"Unknown change {change!r}")
    if touched and change in (CHANGED, REMOVED):
        return (KEEP_MINE, ACCEPT, DISMISS)
    return (ACCEPT, DISMISS)


def can_accept(change: str, *, fuzzy: bool, aligned_for_revision: bool) -> bool:
    """Whether Accept is enabled: a row that brings master bounds needs approved
    alignment for its revision on a fuzzy (tier 4 and below) link."""
    return change == REMOVED or not fuzzy or aligned_for_revision
