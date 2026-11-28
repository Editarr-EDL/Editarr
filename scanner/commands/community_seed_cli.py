"""Build attributed community seed bundles from the external archives and a reviewed catalog crosswalk."""

from __future__ import annotations

import argparse
import collections
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_SOURCES = ("VideoSkip", "Filmiway")


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ms(seconds: float) -> int:
    return int((Decimal(str(seconds)) * 1000).to_integral_value(rounding=ROUND_HALF_UP))


def _work_and_runtime(
    record: dict[str, Any], match: dict[str, Any], source: str
) -> tuple[dict[str, Any], int | None]:
    attribution = record["attribution"]
    if source == "Filmiway":
        title, year = attribution["title"], attribution["year"]
        runtime = attribution.get("runtime_minutes")
    else:
        title, year_text = attribution["title"].rsplit(" (", 1)
        year, runtime = int(year_text.rstrip(")")), None
    series = re.fullmatch(r"(.*) S\d+ E\d+: .*", title) if attribution.get("kind") == "episode" else None
    work = {
        "title": series[1] if series else title, "year": year,
        "kind": "episode" if series else "movie",
        "tmdb": match.get("tmdb"), "tvdb": match.get("tvdb"),
        "season": match.get("season"), "episode": match.get("episode"),
    }
    return work, _ms(runtime * 60) if runtime else None


def prepare_source(
    source: str,
    records: list[dict[str, Any]],
    matches: dict[str, dict[str, Any]],
    taxonomy: dict[str, list[str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Retain unmatched records and incomplete classifications in a source-linked review report."""
    seeds, queue = [], []
    counts: collections.Counter[str] = collections.Counter()
    for record in records:
        record_id = record["id"]
        match = matches.get(record_id, {})
        matched = bool(match.get("tmdb") or match.get("tvdb"))
        counts["records"] += 1
        counts["matched"] += matched
        counts["with_normalized_filters"] += bool(record["filters"])
        counts["normalized_filters"] += len(record["filters"])
        work, duration = _work_and_runtime(record, match, source)
        filters, issues = [], []
        for i, cue in enumerate(record["filters"], 1):
            index = cue.get("source_cue_index", cue.get("line_number", i))
            start, end = _ms(cue["start_seconds"]), _ms(cue["end_seconds"])
            category, subcategory = cue.get("category"), cue.get("subcategory")
            severity, action = cue.get("severity"), cue.get("edit_action")
            if subcategory not in taxonomy.get(category or "", []) or severity not in {"low", "medium", "high"}:
                reason = "incomplete_or_unknown_classification"
            elif action not in {"skip", "mute"}:
                reason = "unsupported_action"
            elif not 0 <= start < end <= (duration or 86_400_000):
                reason = "invalid_bounds"
            elif not matched:
                reason = "unmatched_work"
            else:
                filters.append({
                    "startMs": start, "endMs": end, "category": category, "subcategory": subcategory,
                    "severity": severity, "action": action, "sourceCueIndex": index,
                })
                continue
            counts[reason] += 1
            issues.append({"sourceCueIndex": index, "reason": reason})
        if not matched or issues or record.get("issues"):
            queue.append({
                "sourceRecordId": record_id, "title": record["attribution"]["title"],
                "sourceUrl": record["attribution"]["source_url"], "match": match, "issues": issues,
                "sourceIssues": record.get("issues", []),
            })
        if filters:
            seeds.append({
                "sourceRecordId": record_id, "work": work, "sourceRuntimeMs": duration,
                "provenance": {"attribution": record["attribution"], "sync": record.get("sync"), "match": match},
                "filters": filters,
            })
            counts["seed_records"] += 1
            counts["seed_entries"] += len(filters)
    return (
        {"schemaVersion": "editarr-community-seed-1", "source": source, "records": seeds},
        {"counts": dict(counts), "review": queue},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=_ROOT / "research" / "editarr-seed")
    args = parser.parse_args()
    directory = args.directory
    crosswalk = json.loads((directory / "catalog-crosswalk.json").read_text(encoding="utf-8"))
    taxonomy = json.loads((_ROOT / "community-service/src/artifact/taxonomy.json").read_text(encoding="utf-8"))
    reports = {}
    for source in _SOURCES:
        slug = source.lower()
        records = json.loads((directory / f"{slug}.json").read_text(encoding="utf-8"))["records"]
        bundle, reports[source] = prepare_source(source, records, crosswalk["matches"], taxonomy)
        _write_json(directory / f"{slug}-community.json", bundle)
    _write_json(directory / "community-seed-review.json", reports)
    print(json.dumps({source: report["counts"] for source, report in reports.items()}, indent=2))


if __name__ == "__main__":
    main()
