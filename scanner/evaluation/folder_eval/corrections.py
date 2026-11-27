"""Reviewer corrections of ground truth: storage, normalisation, and effective labels.

`corrections.json` maps item id → {key: bool}; `true` means the key is present,
`false` absent. Every change is also appended to `corrections.log.jsonl`.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .labels import FAMILY_TOKENS, family_of, is_valid_key, with_families
from .paths import Layout, write_json

CORRECTIONS_FILE = "corrections.json"
LOG_FILE = "corrections.log.jsonl"
MAX_NOTE_LENGTH = 1000
_LOCK = threading.Lock()


class CorrectionError(ValueError):
    """A correction request that fails validation."""


def _now() -> str:
    # Microseconds so updated_at also serves as a version stamp for summaries.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def empty_corrections() -> dict[str, Any]:
    return {"version": 1, "updated_at": None, "items": {}}


def corrections_path(layout: Layout) -> Path:
    return layout.results / CORRECTIONS_FILE


def load_corrections(layout: Layout) -> dict[str, Any]:
    """Return the stored corrections, or an empty document when none exist."""
    path = corrections_path(layout)
    if not path.is_file():
        return empty_corrections()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("items"), dict):
        raise ValueError(f"Unsupported corrections file: {path}")
    return payload


def corrections_stamp(layout: Layout) -> tuple[int, int] | None:
    """(mtime_ns, size) of corrections.json, or None; changes on every save."""
    try:
        stat = corrections_path(layout).stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def correction_count(payload: dict[str, Any]) -> int:
    return sum(len(keys) for keys in payload.get("items", {}).values())


def normalise(item_map: dict[str, bool], key: str, value: bool | None) -> dict[str, bool]:
    """Apply one change to an item's map.

    A family set `false` drops `true` corrections on its tokens; a token set `true`
    drops a `false` correction on its family; `None` reverts the key.
    """
    result = dict(item_map)
    if value is None:
        result.pop(key, None)
        return result
    result[key] = value
    family = family_of(key)
    if "/" not in key and value is False:
        for other in [k for k, v in result.items() if v and "/" in k and family_of(k) == family]:
            del result[other]
    if "/" in key and value is True and result.get(family) is False:
        del result[family]
    return result


def effective_expected(expected: list[str], item_map: dict[str, bool]) -> list[str]:
    """Inventory labels with corrections applied (true adds key + family; false removes
    the key and, for a family, all of its tokens)."""
    keys = set(expected)
    for key, value in item_map.items():
        if value:
            continue
        keys.discard(key)
        if "/" not in key:
            keys -= {f"{key}/{token}" for token in FAMILY_TOKENS.get(key, [])}
    keys |= {key for key, value in item_map.items() if value}
    return with_families(keys)


def apply_corrections(
    items: list[dict[str, Any]], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return items with effective `expected`/`scored`; corrected items become scored."""
    corrections = payload.get("items", {})
    if not corrections:
        return items
    result = []
    for item in items:
        item_map = corrections.get(item["id"])
        if item_map:
            item = {
                **item,
                "expected": effective_expected(item["expected"], item_map),
                "scored": True,
                "corrected": True,
            }
        result.append(item)
    return result


def validate_request(body: Any, known_ids) -> tuple[str, str, bool | None, str | None]:
    """Validate a POST body; return (item_id, key, value, note) or raise CorrectionError."""
    if not isinstance(body, dict):
        raise CorrectionError("body must be a JSON object")
    unknown = set(body) - {"item_id", "key", "value", "note"}
    if unknown:
        raise CorrectionError(f"unknown fields: {sorted(unknown)}")
    item_id, key, note = body.get("item_id"), body.get("key"), body.get("note")
    if "value" not in body:
        raise CorrectionError("value is required (true, false, or null)")
    value = body["value"]
    if not isinstance(item_id, str) or item_id not in known_ids:
        raise CorrectionError("unknown item_id")
    if not isinstance(key, str) or not is_valid_key(key):
        raise CorrectionError("key must be an MCF family or family/token")
    if value is not None and not isinstance(value, bool):
        raise CorrectionError("value must be true, false, or null")
    if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE_LENGTH):
        raise CorrectionError(f"note must be a string of at most {MAX_NOTE_LENGTH} characters")
    return item_id, key, value, note


def save_correction(
    layout: Layout, item_id: str, key: str, value: bool | None, note: str | None = None
) -> dict[str, bool]:
    """Persist one validated change atomically, append it to the audit log, and return
    the item's normalised map. Thread-safe within the process."""
    with _LOCK:
        payload = load_corrections(layout)
        items = payload["items"]
        previous = items.get(item_id, {}).get(key)
        item_map = normalise(items.get(item_id, {}), key, value)
        if item_map:
            items[item_id] = item_map
        else:
            items.pop(item_id, None)
        payload["updated_at"] = _now()
        write_json(corrections_path(layout), payload)
        entry: dict[str, Any] = {
            "at": payload["updated_at"], "item_id": item_id, "key": key,
            "value": value, "previous": previous,
        }
        if note:
            entry["note"] = note
        data = (json.dumps(entry, separators=(",", ":")) + "\n").encode("utf-8")
        descriptor = os.open(
            layout.results / LOG_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644
        )
        try:
            os.write(descriptor, data)
        finally:
            os.close(descriptor)
        return item_map
