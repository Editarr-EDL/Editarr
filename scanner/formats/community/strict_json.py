"""Strict JSON and single-member gzip helpers for community master-set formats."""

import gzip
import json
import math
import struct
import zlib

MAX_SAFE_INTEGER = (1 << 53) - 1
_GZIP_MAGIC = b"\x1f\x8b\x08"
_FTEXT = 0x01


class StrictJsonError(ValueError):
    """Bytes are not strict, bounded UTF-8 JSON (or gzip of it)."""


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> object:
    raise StrictJsonError(f"Nonfinite number: {token}")


def _check_tree(value: object, *, max_depth: int | None, max_string_bytes: int | None) -> None:
    """Enforce container depth (root container = 1), string byte length and safe integers."""
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, str):
            try:
                size = len(item.encode("utf-8"))
            except UnicodeError as exc:
                raise StrictJsonError("Invalid Unicode in JSON string") from exc
            if max_string_bytes is not None and size > max_string_bytes:
                raise StrictJsonError("JSON string exceeds the byte limit")
        elif isinstance(item, bool) or item is None:
            continue
        elif isinstance(item, int):
            if abs(item) > MAX_SAFE_INTEGER:
                raise StrictJsonError("JSON integer exceeds Number.MAX_SAFE_INTEGER")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise StrictJsonError("Nonfinite number in JSON")
        elif isinstance(item, (dict, list)):
            if max_depth is not None and depth > max_depth:
                raise StrictJsonError("JSON nesting exceeds the depth limit")
            children = (
                [child for pair in item.items() for child in pair]
                if isinstance(item, dict)
                else item
            )
            stack.extend((child, depth + 1) for child in children)


def parse_strict_json(
    raw: bytes,
    *,
    max_bytes: int,
    max_depth: int | None = None,
    max_string_bytes: int | None = None,
) -> object:
    """Decode bounded UTF-8 JSON without a BOM, duplicate keys or unsafe numbers."""
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise StrictJsonError("JSON exceeds the byte limit")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise StrictJsonError("JSON must not start with a byte order mark")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise StrictJsonError("Invalid UTF-8 JSON") from exc
    _check_tree(value, max_depth=max_depth, max_string_bytes=max_string_bytes)
    return value


def _canonical_numbers(value: object) -> object:
    if isinstance(value, dict):
        return {key: _canonical_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_numbers(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        # Match JavaScript number spelling so an integral float such as 1.0 encodes as 1.
        return int(value)
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Encode compact UTF-8 JSON preserving the caller's key order."""
    try:
        return json.dumps(
            _canonical_numbers(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise StrictJsonError("Value cannot be encoded as strict UTF-8 JSON") from exc


def gzip_single_member(data: bytes) -> bytes:
    """Compress with a fixed header (mtime 0, no name, extra, comment or header CRC)."""
    return gzip.compress(data, compresslevel=9, mtime=0)


def gunzip_single_member(raw: bytes, *, max_compressed: int, max_expanded: int) -> bytes:
    """Expand exactly one gzip member with no optional header fields, within byte limits."""
    if not isinstance(raw, bytes) or len(raw) > max_compressed:
        raise StrictJsonError("Compressed bytes exceed the limit")
    if len(raw) < 18 or not raw.startswith(_GZIP_MAGIC):
        raise StrictJsonError("Not a deflate gzip member")
    if raw[3] & ~_FTEXT:
        raise StrictJsonError("Gzip header flags are not allowed")
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    try:
        expanded = inflater.decompress(raw[10:], max_expanded + 1)
    except zlib.error as exc:
        raise StrictJsonError("Invalid deflate stream") from exc
    if len(expanded) > max_expanded:
        raise StrictJsonError("Expanded bytes exceed the limit")
    if not inflater.eof or len(inflater.unused_data) != 8:
        raise StrictJsonError("Gzip must contain exactly one complete member")
    crc, size = struct.unpack("<II", inflater.unused_data)
    if crc != zlib.crc32(expanded) or size != len(expanded) & 0xFFFFFFFF:
        raise StrictJsonError("Gzip trailer does not match the expanded bytes")
    return expanded
