"""Convert archived third-party cues into an offline Editarr review seed."""

import argparse
import json
import os
import re
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from scanner.formats.mcf_export import export_mcf_content
from scanner.formats.mcf_types import McfCueEntry
from scanner.formats.videoskip import VideoSkipParseError, parse_videoskip
from scanner.models import (
    MCF_CATEGORY_TO_FAMILY,
    McfCategory,
    McfChannel,
    McfWorkType,
    ReviewMcfDocument,
    Severity,
)

_SEVERITIES = {
    "mild": Severity.LOW,
    "low": Severity.LOW,
    "moderate": Severity.MEDIUM,
    "medium": Severity.MEDIUM,
    "high": Severity.HIGH,
    "severe": Severity.HIGH,
}


def _json(path: Path):
    return json.loads(path.read_bytes())


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


def _bundle(output: Path, source: str, reuse_status: str, records, **extra) -> None:
    bundle = {
        "schema_version": 1,
        "format": "editarr-external-review-seed",
        "source": source,
        "reuse_status": reuse_status,
        "timing": "source_coordinates_require_target_verification",
        "counts": {
            "records": len(records),
            "records_with_filters": sum(bool(record["filters"]) for record in records),
            "source_cues": sum(len(record["source_cues"]) for record in records),
            "editarr_filters": sum(len(record["filters"]) for record in records),
            "issues": sum(len(record["issues"]) for record in records),
        },
        **extra,
        "records": records,
    }
    raw = json.dumps(bundle, ensure_ascii=False, allow_nan=False, indent=2)
    _write(output, (raw + "\n").encode())


def _videoskip_records(manifest: Path, output: Path):
    data = _json(manifest)
    archive = manifest.parent.resolve()
    records = []
    for item in sorted(data["files"], key=lambda item: item["id"]):
        attribution = {key: value for key, value in item.items() if key != "file"}
        record = {
            "id": f"videoskip:{item['id']}",
            "attribution": attribution,
            "source_file": None,
            "sync": None,
            "source_cues": [],
            "filters": [],
            "issues": [],
        }
        records.append(record)
        if item["status"] != "downloaded":
            record["issues"].append({"code": "source_file_unavailable"})
            continue
        local = (archive / item["file"]).resolve()
        if not local.is_relative_to(archive) or local.suffix.lower() != ".skp":
            raise ValueError(f"Invalid VideoSkip archive path for ID {item['id']}")
        content = local.read_bytes()
        if sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"VideoSkip checksum mismatch for ID {item['id']}")
        record["source_file"] = os.path.relpath(local, output)
        try:
            parsed = parse_videoskip(content)
        except VideoSkipParseError as exc:
            record["issues"].append({
                "code": exc.code, "message": str(exc), "line_number": exc.line_number,
            })
            continue
        record["sync"] = parsed.sync.model_dump(mode="json")
        record["source_cues"] = [asdict(cue) for cue in parsed.cues]
        record["filters"] = [asdict(row) for row in parsed.to_import().filters]
        record["issues"] = [issue.model_dump(mode="json") for issue in parsed.issues]
    return data, records


def _filmiway_categories(label: str) -> list[str]:
    label = label.casefold()
    return [
        token
        for pattern, token in (
            (r"nud", "nudity"),
            (r"masturb", "masturbation"),
            (r"sex|suggest|lingerie", "sex"),
            (r"violen|gore|blood", "violence"),
            (r"profan|language", "swearing"),
            (r"fright|scare|intense", "fear"),
            (r"alcohol", "alcohol"),
            (r"drugs|substance", "drugs"),
        )
        if re.search(pattern, label) and (token != "sex" or "masturb" not in label)
    ]


def _filmiway_filters(movie, record):
    runtime = movie["runtime_minutes"] * 60
    entries = []
    for number, mark in enumerate(movie["scenes"], 1):
        start, end = mark["start_seconds"], mark["end_seconds"]
        categories = _filmiway_categories(mark["source_category"])
        severity = _SEVERITIES.get((mark["source_severity"] or "").casefold())
        if mark.get("timing_issue") or not 0 <= start < end <= runtime:
            record["issues"].append({"code": "outside_reported_runtime", "cue_index": number})
            continue
        if not categories or severity is None or mark["source_action"] != "skip":
            record["issues"].append({"code": "unmapped_cue", "cue_index": number})
            continue
        for position, token in enumerate(categories, 1):
            family = MCF_CATEGORY_TO_FAMILY[token]
            record["filters"].append({
                "start_seconds": start, "end_seconds": end, "category": family,
                "subcategory": token, "severity": severity, "mcf_channel": McfChannel.BOTH,
                "source": "visual", "edit_action": "skip", "source_cue_index": number,
            })
            entries.append(McfCueEntry(
                cue_index=number, payload_index=position, line_number=number,
                start_seconds=start, end_seconds=end, category=family,
                subcategory=McfCategory(token), severity=severity, mcf_channel=McfChannel.BOTH,
                mcf_comment=mark["source_category"],
            ))
    return entries


def _filmiway_records(source: Path, output: Path):
    data = _json(source)
    records = []
    for movie in data["movies"]:
        slug = urlsplit(movie["source_url"]).path.split("/")[2]
        if not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError("Invalid Filmiway movie slug")
        record = {
            "id": f"filmiway:{slug}",
            "attribution": {"publisher": "Filmiway", **{
                key: value for key, value in movie.items() if key != "scenes"
            }},
            "source_cues": movie["scenes"],
            "filters": [],
            "issues": [],
        }
        records.append(record)
        entries = _filmiway_filters(movie, record)
        if not entries:
            continue
        path = Path("filmiway-mcf") / f"{slug}.mcf"
        document = ReviewMcfDocument(
            title=movie["title"],
            year=movie["year"],
            work_type=McfWorkType.MOVIE,
            source_url=movie["source_url"],
            release=movie.get("edition"),
            comment="Source: Filmiway. Reported runtime is approximate. Verify the local cut.",
            material_start_seconds=0,
            material_end_seconds=movie["runtime_minutes"] * 60,
        )
        _write(output / path, export_mcf_content(document, entries))
        record["import_file"] = path.as_posix()
    return data, records


def main(argv: list[str] | None = None) -> None:
    """Build both JSON datasets and Filmiway MCF files without touching a database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videoskip", type=Path, required=True, help="VideoSkip manifest.json")
    parser.add_argument("--filmiway", type=Path, required=True, help="Filmiway recovery JSON")
    parser.add_argument("--output", type=Path, required=True, help="Local seed directory")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    video_data, videos = _videoskip_records(args.videoskip, output)
    film_data, films = _filmiway_records(args.filmiway, output)
    _bundle(
        output / "videoskip.json", "VideoSkip Exchange", video_data["reuse_status"], videos,
        archive_manifest=os.path.relpath(args.videoskip.resolve(), output),
    )
    _bundle(
        output / "filmiway.json", "Filmiway", film_data["reuse_status"], films,
        archive_manifest=os.path.relpath(args.filmiway.resolve(), output),
        unrecovered_guide_urls=film_data["unrecovered_guide_urls"],
    )


if __name__ == "__main__":
    main()
