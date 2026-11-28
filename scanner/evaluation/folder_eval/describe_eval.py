"""`describe-eval`: score model descriptions and severity against window labels.

Each description is judged on its TEXT alone by d1-3B (state = the description, one
noul question per key: does it say violence / sex / nudity / drugs / blood or gore is
shown? Negated mentions such as "no nudity is visible" do not count). A mention on a
positive window is a hit, on a negative window a hallucination.
Judgments are cached in `describe_judgments.json` by description hash. Severity levels
are compared with the tag-derived ground truth (`severity.window_severity`).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .corrections import apply_corrections, load_corrections
from .describe import is_none_description
from .paths import Layout, write_json
from .results import load_results, write_model_index
from .severity import SEVERITY_KEYS, SeverityMap, severity_metrics, window_severity
from .subsets import load_subset, positive_keys

log = logging.getLogger(__name__)
MENTION_KEYS = ("violence", "sex", "nudity", "drugs", "fear/hemophobia")
MENTION_SUBJECTS = {
    "violence": "violence",
    "sex": "sex or sexual activity",
    "nudity": "nudity",
    "drugs": "drugs, alcohol or smoking",
    "fear/hemophobia": "blood or gore",
}
MENTION_THRESHOLD = 0.5
JUDGMENTS_FILE = "describe_judgments.json"


def judge_body(description: str) -> dict:
    """Text-only /v1/systemone request: one noul question per mention key."""
    return {
        "state": description,
        "questions": {
            f"m{index}": {
                "type": "noul",
                "instructions": (
                    f"Does this description say that {MENTION_SUBJECTS[key]} is shown? "
                    "Content it says is absent does not count."
                ),
            }
            for index, key in enumerate(MENTION_KEYS)
        },
    }


def parse_judgment(body: dict) -> dict[str, float]:
    answers = body.get("answers") or {}
    return {key: float(answers[f"m{index}"]["noul"]) for index, key in enumerate(MENTION_KEYS)}


# Bump when the judge questions change so cached judgments are not reused.
JUDGE_VERSION = "2"


def text_hash(text: str) -> str:
    return hashlib.sha1(f"{JUDGE_VERSION}\n{text}".encode()).hexdigest()


class Judge:
    """Cached d1-3B text judge; the server starts only for uncached descriptions."""

    def __init__(self, cache_path: Path, backend_factory=None):
        self.cache_path = cache_path
        self.cache: dict[str, dict[str, float]] = (
            json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
        )
        self._factory = backend_factory
        self._backend = None

    def mentions(self, description: str) -> dict[str, float]:
        if is_none_description(description):
            return dict.fromkeys(MENTION_KEYS, 0.0)
        digest = text_hash(description)
        if digest not in self.cache:
            if self._backend is None:
                self._backend = (self._factory or _default_judge)()
                self._backend._ensure_server()
            self.cache[digest] = parse_judgment(self._backend._post(judge_body(description)))
        return self.cache[digest]

    def close(self) -> None:
        write_json(self.cache_path, self.cache)
        if self._backend is not None:
            self._backend.close()
            self._backend = None


def _default_judge():
    from .decision.llama_backend import LlamaServerDecisionBackend
    from .decision.specs import SPECS

    return LlamaServerDecisionBackend(SPECS["d1-3b-full4"])


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def evaluate_model(rows: list[dict[str, Any]], judge: Judge | None) -> dict[str, Any]:
    """Metrics for one model; rows hold `expected`, `truth_severity` and the frame."""
    frames = [row["frame"] for row in rows]
    statuses = [frame.get("describe") for frame in frames if "description" in frame]
    attempted = [status for status in statuses if status != "skipped"]
    result: dict[str, Any] = {"frames": len(rows)}
    measured = [frame["logprob_fallback"] for frame in frames if "logprob_fallback" in frame]
    if measured:
        # Share of boolean values scored from the generated value (alternative not in top-k).
        result["logprob_fallback_rate"] = _ratio(sum(map(len, measured)), 5 * len(measured))
    described = [row for row in rows if "description" in row["frame"]]
    if described:
        result["json_failure_rate"] = _ratio(attempted.count("failed"), len(attempted))
        result["json_retry_rate"] = _ratio(attempted.count("retried"), len(attempted))
        result["describe_skipped"] = statuses.count("skipped")
        positive_rows = [row for row in described if positive_keys(row["expected"], MENTION_KEYS)]
        result["none_on_positive_windows"] = _ratio(
            sum(is_none_description(row["frame"]["description"]) for row in positive_rows),
            len(positive_rows),
        )
        per_key = {}
        judged = [(row, judge.mentions(row["frame"]["description"] or "none")) for row in described]
        for key in MENTION_KEYS:
            positives = [
                p[key] >= MENTION_THRESHOLD
                for row, p in judged
                if key in positive_keys(row["expected"], MENTION_KEYS)
            ]
            negatives = [
                p[key] >= MENTION_THRESHOLD
                for row, p in judged
                if key not in positive_keys(row["expected"], MENTION_KEYS)
            ]
            hits, false_mentions = sum(positives), sum(negatives)
            per_key[key] = {
                "positives": len(positives),
                "negatives": len(negatives),
                "hits": hits,
                "hallucinations": false_mentions,
                "precision": _ratio(hits, hits + false_mentions),
                "recall": _ratio(hits, len(positives)),
                "hallucination_rate": _ratio(false_mentions, len(negatives)),
            }
        result["description"] = per_key
    # Frames that attempted severity; a failed reply (null) or a missing key is unusable.
    severity_rows = [
        row for row in rows if "severity" in row["frame"] and row["truth_severity"] is not None
    ]
    if severity_rows:
        severity_map = SeverityMap.load()
        result["severity"] = {
            key: severity_metrics(
                [row["truth_severity"][key] for row in severity_rows],
                [(row["frame"]["severity"] or {}).get(key) for row in severity_rows],
                severity_map.max_level[key],
            )
            for key in SEVERITY_KEYS
        }
    return result


def describe_eval(layout: Layout, subset_name: str, models: list[str] | None = None) -> dict:
    """Write `describe_eval.json` for models with descriptions or severity on a subset."""
    inventory = layout.load_inventory()["items"]
    corrections = load_corrections(layout)
    items = {item["id"]: item for item in apply_corrections(inventory, corrections)}
    raw = {item["id"]: item for item in inventory}
    severity_map = SeverityMap.load()
    subset = load_subset(layout, subset_name)
    judge = Judge(layout.results / JUDGMENTS_FILE)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "subset": subset_name,
        "mention_threshold": MENTION_THRESHOLD,
        "models": {},
    }
    try:
        for model_id in models or write_model_index(layout):
            results = load_results(layout.model_jsonl(model_id))
            rows = []
            for identifier, wanted in subset.items():
                record = results.get(identifier)
                if not record or record.get("status") != "ok":
                    continue
                by_n = {frame["n"]: frame for frame in record.get("frames", [])}
                for n in wanted:
                    frame = by_n.get(n)
                    if frame and ("description" in frame or "severity" in frame):
                        rows.append(
                            {
                                "expected": items[identifier]["expected"],
                                "truth_severity": window_severity(
                                    raw[identifier],
                                    corrections.get("items", {}).get(identifier),
                                    severity_map,
                                ),
                                "frame": frame,
                            }
                        )
            if rows:
                log.info("describe-eval %s: %d frames", model_id, len(rows))
                report["models"][model_id] = evaluate_model(rows, judge)
    finally:
        judge.close()
    write_json(layout.results / "describe_eval.json", report)
    return report
