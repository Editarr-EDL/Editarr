"""Experimental, synthetic JSON/typed-Protobuf community payload benchmark.

No production artifact schema or encoding is defined by this module.
"""

import argparse
import base64
import gzip
import hashlib
import json
import platform
import sqlite3
import statistics
import sys
import time
import tracemalloc
from collections.abc import Iterable
from pathlib import Path

import google.protobuf
from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory


def _message_type():
    file = descriptor_pb2.FileDescriptorProto(name="community_benchmark.proto", package="bench")
    file.syntax = "proto3"
    string = descriptor_pb2.FieldDescriptorProto.TYPE_STRING
    number = descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE
    message = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    unsigned = descriptor_pb2.FieldDescriptorProto.TYPE_UINT32

    def add(name: str, fields: Iterable[tuple[str, int, bool, str | None]]) -> None:
        record = file.message_type.add(name=name)
        for index, (field_name, kind, repeated, type_name) in enumerate(fields, 1):
            field = record.field.add(name=field_name, number=index, type=kind)
            field.label = (
                descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
                if repeated else descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
            )
            if type_name:
                field.type_name = f".bench.{type_name}"
            if field_name in {"model_id", "model_revision", "model_digest", "quantization",
                              "build_revision", "sampling_seconds", "parent_digest"}:
                field.oneof_index = len(record.oneof_decl)
                record.oneof_decl.add(name=f"_{field_name}")
                field.proto3_optional = True

    add("Interval", [("start_seconds", number, False, None),
                     ("end_seconds", number, False, None)])
    add("Stage", [("stage", string, False, None), ("execution_id", string, False, None),
                  ("status", string, False, None), ("model_id", string, False, None),
                  ("model_revision", string, False, None),
                  ("settings_json", string, False, None),
                  ("intervals", message, True, "Interval"),
                  ("release_version", string, False, None),
                  ("scanner_behavior_version", string, False, None),
                  ("build_revision", string, False, None),
                  ("model_digest", string, False, None),
                  ("quantization", string, False, None),
                  ("sampling_seconds", number, False, None),
                  ("source", string, False, None)])
    add("Event", [("event_id", string, False, None),
                  ("start_seconds", number, False, None),
                  ("end_seconds", number, False, None),
                  ("category", string, False, None), ("subcategory", string, False, None),
                  ("severity", string, False, None), ("source", string, False, None),
                  ("note", string, False, None)])
    add("Artifact", [("schema_version", string, False, None),
                     ("submission_id", string, False, None),
                     ("kind", string, False, None),
                     ("work_id", string, False, None),
                     ("release", string, False, None),
                     ("scanner_version", string, False, None),
                     ("parent_digest", string, False, None),
                     ("stages", message, True, "Stage"),
                     ("events", message, True, "Event")])
    add("Summary", [("schema_version", string, False, None),
                    ("submission_id", string, False, None),
                    ("kind", string, False, None),
                    ("work_id", string, False, None),
                    ("release", string, False, None),
                    ("scanner_version", string, False, None),
                    ("parent_digest", string, False, None),
                    ("stages", message, True, "Stage"),
                    ("event_count", unsigned, False, None)])
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file)
    return tuple(message_factory.GetMessageClass(pool.FindMessageTypeByName(name))
                 for name in ("bench.Artifact", "bench.Summary"))


ARTIFACT, SUMMARY = _message_type()


def fixtures() -> dict[str, dict]:
    """Generate bounded, sanitized examples without reading user media or databases."""
    common = {
        "schema_version": "experiment-1", "submission_id": "synthetic-0001",
        "kind": "generated", "work_id": "movie:synthetic", "release": "test-1080p",
        "scanner_version": "scanner-1", "parent_digest": None,
        "stages": [{
            "stage": "visual", "execution_id": "attempt-a", "status": "complete",
            "model_id": "nudenet", "model_revision": None,
            "settings_json": '{"coarse_scan_enabled":false}',
            "intervals": [{"start_seconds": 0.0, "end_seconds": 5400.0}],
        }], "events": [],
    }

    def event(index: int) -> dict:
        return {
            "event_id": f"event-{index:06d}", "start_seconds": float(index * 7),
            "end_seconds": float(index * 7 + 2), "category": "language",
            "subcategory": "swearing", "severity": "medium", "source": "audio",
            "note": f"Synthetic context for event {index % 17}.",
        }

    small = {**common, "events": [event(index) for index in range(12)]}
    large = {**common, "events": [event(index) for index in range(2500)]}
    zero = dict(common)
    partial = {**common, "stages": [
        common["stages"][0],
        {"stage": "visual_qwen:scene_0001", "execution_id": "attempt-a",
         "status": "partial", "model_id": "qwen3-vl-4b-instruct-4bit",
         "model_revision": None, "settings_json": '{}', "intervals": []},
        {"stage": "audio_scan", "execution_id": "attempt-b", "status": "complete",
         "model_id": "whisper", "model_revision": None,
         "settings_json": '{"audio_model":"whisper"}', "intervals": []},
        {"stage": "visual_qwen:scene_0002", "execution_id": "attempt-b",
         "status": "partial", "model_id": None, "model_revision": None,
         "settings_json": '{"skip_reason":"cached_result"}', "intervals": []},
    ], "events": [event(index) for index in range(25)]}
    manual = {**common, "kind": "manual", "stages": [], "events": [
        {**event(index), "source": "manual", "note": "Reviewed synthetic note"}
        for index in range(5)
    ]}
    derived = {**manual, "kind": "derived", "parent_digest": "a" * 64,
               "events": [{**event(index), "source": "derived"} for index in range(20)]}
    examples = {"small": small, "large": large, "zero": zero,
                "partial": partial, "manual": manual, "derived": derived}
    for artifact in examples.values():
        for stage in artifact["stages"]:
            stage.update({
                "release_version": "0.1.0", "scanner_behavior_version": "scanner-1",
                "build_revision": None, "model_digest": None, "quantization": None,
                "sampling_seconds": None, "source": "generated",
            })
    return examples


def encode_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def summary(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "events"} | {
        "event_count": len(payload["events"]),
    }


def encode_proto(payload: dict, *, as_summary: bool = False) -> bytes:
    record = json_format.ParseDict(payload, SUMMARY() if as_summary else ARTIFACT())
    return record.SerializeToString(deterministic=True)


def decode_proto(raw: bytes, *, as_summary: bool = False) -> dict:
    record = SUMMARY() if as_summary else ARTIFACT()
    record.ParseFromString(raw)
    decoded = json_format.MessageToDict(
        record, preserving_proto_field_name=True, always_print_fields_with_no_presence=True,
    )
    decoded.setdefault("parent_digest", None)
    for stage in decoded["stages"]:
        for key in ("model_id", "model_revision", "model_digest", "quantization",
                "build_revision", "sampling_seconds"):
            stage.setdefault(key, None)
    return decoded


def _measure(operation, iterations: int) -> dict:
    samples = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    tracemalloc.start()
    try:
        operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {"median_ms": round(statistics.median(samples), 3),
            "p95_ms": round(sorted(samples)[int((len(samples) - 1) * 0.95)], 3),
            "python_peak_bytes": peak}


def _sqlite_proxy_size(payload: dict, *, binary: bool) -> int:
    """Compare SQLite indexed-row pages, not actual Cloudflare D1 billing."""
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE catalog (submission_id TEXT PRIMARY KEY, work_id TEXT, "
                   "kind TEXT, summary BLOB)")
        db.execute("CREATE INDEX catalog_lookup ON catalog(work_id, kind)")
        encoded = (encode_proto(payload, as_summary=True) if binary
                   else encode_json(payload).decode("utf-8"))
        db.executemany(
            "INSERT INTO catalog VALUES (?, ?, ?, ?)",
            ((f"id-{index:04d}", payload["work_id"], payload["kind"], encoded)
             for index in range(100)),
        )
        return db.execute("PRAGMA page_count").fetchone()[0] * db.execute(
            "PRAGMA page_size"
        ).fetchone()[0]


def benchmark(iterations: int = 20) -> dict:
    """Measure local codec, gzip, SHA-256 and SQLite proxies over synthetic data."""
    if iterations < 2:
        raise ValueError("iterations must be at least 2")
    results = {}
    for name, artifact in fixtures().items():
        cases = {}
        for surface, payload, is_summary in (
            ("r2_body", artifact, False), ("d1_summary", summary(artifact), True),
        ):
            json_bytes = encode_json(payload)
            proto_bytes = encode_proto(payload, as_summary=is_summary)
            assert json.loads(json_bytes) == decode_proto(proto_bytes, as_summary=is_summary)
            sizes = {}
            for encoding, raw, encode, decode in (
                ("json", json_bytes, lambda: encode_json(payload),
                 lambda: json.loads(json_bytes)),
                ("protobuf", proto_bytes,
                 lambda: encode_proto(payload, as_summary=is_summary),
                 lambda: decode_proto(proto_bytes, as_summary=is_summary)),
            ):
                compressed = gzip.compress(raw, mtime=0)
                sizes[encoding] = {
                    "raw_bytes": len(raw), "gzip_bytes": len(compressed),
                    "encode": _measure(encode, iterations),
                    "decode": _measure(decode, iterations),
                    "gzip_decompress_sha256": _measure(
                        lambda: hashlib.sha256(gzip.decompress(compressed)).digest(),
                        iterations,
                    ),
                }
            if is_summary:
                sizes["sqlite_indexed_100_rows_bytes"] = {
                    "json": _sqlite_proxy_size(payload, binary=False),
                    "protobuf": _sqlite_proxy_size(payload, binary=True),
                }
            cases[surface] = sizes
        results[name] = cases
    return {
        "environment": {"python": sys.version.split()[0], "protobuf": google.protobuf.__version__,
                        "platform": platform.platform(), "iterations": iterations,
                        "sqlite": sqlite3.sqlite_version, "compression": "gzip mtime=0"},
        "fixtures": results,
    }


def vectors() -> dict[str, dict[str, dict[str, str | int]]]:
    """Golden lengths and hashes for a separate TypeScript implementation."""
    result = {}
    for name, artifact in fixtures().items():
        result[name] = {}
        for surface, payload, as_summary in (
            ("r2_body", artifact, False), ("d1_summary", summary(artifact), True),
        ):
            result[name][surface] = {}
            for encoding, raw in (("json", encode_json(payload)),
                                  ("protobuf", encode_proto(payload, as_summary=as_summary))):
                result[name][surface][encoding] = {
                    "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--vectors", action="store_true")
    parser.add_argument("--export-vectors", type=Path)
    args = parser.parse_args()
    if args.export_vectors:
        if args.export_vectors.name != "encoding-vectors.json":
            parser.error("export target must be encoding-vectors.json")
        payload = {
            "descriptor_base64": base64.b64encode(ARTIFACT.DESCRIPTOR.file.serialized_pb).decode(),
            "vectors": vectors(),
        }
        args.export_vectors.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
        return
    print(json.dumps(vectors() if args.vectors else benchmark(args.iterations), indent=2,
                     sort_keys=True))


if __name__ == "__main__":
    main()
