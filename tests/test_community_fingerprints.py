"""Production byte contract, file mutations and independent cache invalidation."""

import hashlib
import io
import json
import os
import struct
from pathlib import Path

import pytest

from scanner.formats.community.fingerprints import (
    FULL_ALGORITHM,
    SAMPLE_BYTES,
    SAMPLED_ALGORITHM,
    digest_stream,
    sample_ranges,
)
from scanner.integrations.community import fingerprints as files


@pytest.mark.parametrize(
    "size",
    [
        0,
        1,
        SAMPLE_BYTES - 1,
        SAMPLE_BYTES,
        SAMPLE_BYTES + 1,
        9 * SAMPLE_BYTES - 1,
        9 * SAMPLE_BYTES,
        9 * SAMPLE_BYTES + 1,
        17 * SAMPLE_BYTES,
    ],
)
def test_boundary_bytes_match_independent_contract(size):
    content = (bytes(range(256)) * (size // 256 + 1))[:size]
    if size <= 9 * SAMPLE_BYTES:
        ranges = ((0, size),) if size else ()
    elif size == 9 * SAMPLE_BYTES + 1:
        ranges = ((0, 8 * SAMPLE_BYTES), (8 * SAMPLE_BYTES + 1, SAMPLE_BYTES))
    else:
        ranges = tuple(((size - SAMPLE_BYTES) * index // 8, SAMPLE_BYTES) for index in range(9))
    assert sample_ranges(size) == ranges
    assert sum(length for _, length in ranges) <= 9 * SAMPLE_BYTES
    reference = hashlib.sha256(
        SAMPLED_ALGORITHM.encode("ascii") + b"\0" + struct.pack(">QI", size, len(ranges))
    )
    for offset, length in ranges:
        reference.update(struct.pack(">QQ", offset, length))
        reference.update(content[offset : offset + length])
    assert digest_stream(io.BytesIO(content), size, SAMPLED_ALGORITHM) == reference.hexdigest()
    assert (
        digest_stream(io.BytesIO(content), size, FULL_ALGORITHM)
        == hashlib.sha256(content).hexdigest()
    )


def test_unknown_algorithm_invalid_size_and_short_read():
    with pytest.raises(ValueError):
        digest_stream(io.BytesIO(b"x"), 1, "md5")
    for size in (-1, 1 << 64):
        with pytest.raises(ValueError):
            sample_ranges(size)
    with pytest.raises(OSError):
        digest_stream(io.BytesIO(b"x"), 2, SAMPLED_ALGORITHM)


def test_cache_reuses_stable_file_and_separates_full(tmp_path, monkeypatch):
    path = tmp_path / "video"
    path.write_bytes(b"content")
    cache = files.FingerprintCache()
    sampled = cache.fingerprint(path)
    full = cache.fingerprint(path, full=True)
    assert sampled.algorithm != full.algorithm
    assert full.digest == hashlib.sha256(b"content").hexdigest()
    assert full.ranges == ()
    monkeypatch.setattr(files, "digest_stream", lambda *_args: pytest.fail("Unexpected read"))
    assert cache.fingerprint(path) is sampled
    assert cache.fingerprint(path, full=True) is full


def test_same_size_edit_restored_mtime_invalidates_cache(tmp_path):
    path = tmp_path / "video"
    path.write_bytes(b"before")
    previous = path.stat()
    cache = files.FingerprintCache()
    original = cache.fingerprint(path)
    path.write_bytes(b"after!")
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert cache.fingerprint(path).digest != original.digest


def test_rename_and_replacement(tmp_path):
    path = tmp_path / "video"
    path.write_bytes(b"before")
    cache = files.FingerprintCache()
    original = cache.fingerprint(path)
    renamed = tmp_path / "renamed"
    path.rename(renamed)
    assert cache.fingerprint(renamed).digest == original.digest
    path.write_bytes(b"after!")
    path.replace(renamed)
    assert cache.fingerprint(renamed).digest != original.digest


@pytest.mark.parametrize("mutation", ["append", "truncate", "replace", "same-size", "delete"])
def test_mutation_during_read_never_publishes_result(tmp_path, monkeypatch, mutation):
    path = tmp_path / "video"
    path.write_bytes(b"original")
    cache = files.FingerprintCache()
    real_digest = files.digest_stream

    def changing_digest(stream, size, algorithm):
        digest = real_digest(stream, size, algorithm)
        if mutation == "append":
            with path.open("ab") as output:
                output.write(b"more")
        elif mutation == "truncate":
            path.write_bytes(b"x")
        elif mutation == "replace":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"original")
            replacement.replace(path)
        elif mutation == "same-size":
            metadata = path.stat()
            path.write_bytes(b"modified")
            os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        else:
            path.unlink()
        return digest

    monkeypatch.setattr(files, "digest_stream", changing_digest)
    with pytest.raises(files.FileChangedError):
        cache.fingerprint(path)
    assert not cache._entries
    monkeypatch.setattr(files, "digest_stream", real_digest)
    path.write_bytes(b"retry")
    assert cache.fingerprint(path).digest == real_digest(io.BytesIO(b"retry"), 5, SAMPLED_ALGORITHM)


def test_changed_cache_hit_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "video"
    path.write_bytes(b"original")
    cache = files.FingerprintCache()
    cache.fingerprint(path)
    real_check = files._check_state
    checks = 0

    def changing_check(selected, stream, expected):
        nonlocal checks
        checks += 1
        if checks == 2:
            path.write_bytes(b"modified")
        real_check(selected, stream, expected)

    monkeypatch.setattr(files, "_check_state", changing_check)
    with pytest.raises(files.FileChangedError):
        cache.fingerprint(path)
    assert not cache._entries


def test_bounded_cache_and_missing_file(tmp_path):
    cache = files.FingerprintCache(capacity=1)
    for index in range(2):
        path = tmp_path / str(index)
        path.write_bytes(bytes([index]))
        cache.fingerprint(path)
    assert len(cache._entries) == 1
    with pytest.raises(FileNotFoundError):
        cache.fingerprint(tmp_path / "missing")
    assert not cache._entries
    with pytest.raises(ValueError):
        files.FingerprintCache(capacity=0)


def test_shared_fixed_vectors():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/community/file-fingerprints-v1.json").read_text()
    )
    assert fixture["algorithm"] == SAMPLED_ALGORITHM
    for vector in fixture["vectors"]:
        size = vector["size_bytes"]
        content = (bytes(range(256)) * (size // 256 + 1))[:size]
        assert [list(item) for item in sample_ranges(size)] == vector["ranges"]
        assert (
            digest_stream(io.BytesIO(content), size, SAMPLED_ALGORITHM) == vector["sampled_sha256"]
        )
        assert digest_stream(io.BytesIO(content), size, FULL_ALGORITHM) == vector["full_sha256"]


def test_production_read_budget_and_unsampled_change(tmp_path):
    size = 17 * SAMPLE_BYTES
    content = bytearray(size)

    class CountingStream(io.BytesIO):
        bytes_read = 0

        def read(self, size=-1):
            chunk = super().read(size)
            self.bytes_read += len(chunk)
            return chunk

    stream = CountingStream(content)
    digest_stream(stream, size, SAMPLED_ALGORITHM)
    assert stream.bytes_read == 9 * SAMPLE_BYTES
    path = tmp_path / "video"
    path.write_bytes(content)
    cache = files.FingerprintCache()
    original = cache.fingerprint(path)
    full = cache.fingerprint(path, full=True)
    content[SAMPLE_BYTES] = 1
    path.write_bytes(content)
    changed = cache.fingerprint(path)
    assert changed is not original
    assert changed.digest == original.digest
    assert cache.fingerprint(path, full=True).digest != full.digest
    copy = tmp_path / "copy"
    copy.write_bytes(content)
    assert cache.fingerprint(copy).digest == changed.digest
