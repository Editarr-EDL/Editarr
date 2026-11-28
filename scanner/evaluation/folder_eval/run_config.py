"""Scoring-configuration fingerprints, so a resumed run never mixes configurations.

Every result line records the fingerprint of the configuration that scored it: the
backend class and id, its meta (artifact, runtime build, thresholds, stage-2 threshold,
image long edge, supported keys) and, where the backend has them, its spec and question
set. `run` resumes only when every stored line carries the current fingerprint; lines
without one (written before fingerprints existed) are treated as unknown, so the safe
choice is to start again with `--restart`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import Layout

FIELD = "config"
# Display-only meta fields that never change scores.
_DISPLAY_ONLY = ("notes", "display_name")


class ConfigMismatch(RuntimeError):
    """Stored results were scored with another (or an unknown) configuration."""


def scoring_config(backend: Any) -> dict[str, Any]:
    """The settings that affect a backend's scores, as JSON-compatible data."""
    meta = {key: value for key, value in backend.meta().items() if key not in _DISPLAY_ONLY}
    config: dict[str, Any] = {"backend": type(backend).__name__, "meta": meta}
    for name in ("spec", "questions"):
        value = getattr(backend, name, None)
        if value is not None:
            config[name] = repr(value)
    return config


def fingerprint(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def stored_fingerprints(path: Path) -> set[str | None]:
    """Fingerprints recorded in a model's jsonl; None stands for lines without one."""
    found: set[str | None] = set()
    if not path.is_file():
        return found
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # blank separators and a torn last line
            if isinstance(record, dict):
                found.add(record.get(FIELD))
    return found


def check_resume(path: Path, model_id: str, current: str) -> None:
    """Raise ConfigMismatch unless every stored line was scored with `current`."""
    stored = stored_fingerprints(path)
    if not stored or stored == {current}:
        return
    if None in stored:
        problem = "has lines without a scoring-configuration fingerprint (an older run)"
    else:
        others = ", ".join(sorted(str(value) for value in stored - {current}))
        problem = f"was scored with another configuration ({others}; now {current})"
    raise ConfigMismatch(
        f"{path.name} {problem}; resuming would mix configurations. Rerun with "
        f"`run --model {model_id} --restart` to archive it and start again, or score this "
        "configuration under another model id."
    )


def archive_results(layout: Layout, model_id: str) -> list[Path]:
    """Rename a model's jsonl and meta.json to `<name>.<UTC time>.bak`; return the archives."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archived = []
    for path in (layout.model_jsonl(model_id), layout.model_meta(model_id)):
        if path.exists():
            target = path.with_name(f"{path.name}.{stamp}.bak")
            path.rename(target)
            archived.append(target)
    return archived
