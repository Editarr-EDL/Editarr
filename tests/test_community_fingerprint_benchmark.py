"""Independent layout and bounded-read checks for CSS-35 candidate experiments."""

import hashlib

import pytest

from scripts.benchmark_community_fingerprints import (
    CANDIDATES,
    MIB,
    benchmark,
    hash_candidate,
    sample_ranges,
    write_synthetic,
)


@pytest.mark.parametrize("candidate", CANDIDATES)
@pytest.mark.parametrize("size", [0, 1, MIB - 1, MIB, MIB + 1, 8 * MIB, 10 * MIB, 256 * MIB])
def test_layout_is_disjoint_bounded_and_covers_edges(candidate, size):
    ranges = sample_ranges(size, candidate)
    count, width = CANDIDATES[candidate]
    assert sum(length for _, length in ranges) <= min(size, count * width)
    if not size:
        assert ranges == ()
        return
    assert ranges[0][0] == 0
    assert sum(ranges[-1]) == size
    assert all(length > 0 and offset + length <= size for offset, length in ranges)
    assert all(sum(left) < right[0] for left, right in zip(ranges, ranges[1:]))
    if size <= count * width:
        assert ranges == ((0, size),)


def test_exact_integer_offsets_and_overlap_boundaries():
    assert sample_ranges(20 * MIB, "edges-2x4m") == ((0, 4 * MIB), (16 * MIB, 4 * MIB))
    assert sample_ranges(18 * MIB, "spread-5x2m") == tuple(
        (index * 4 * MIB, 2 * MIB) for index in range(5)
    )
    assert sample_ranges(17 * MIB, "spread-9x1m") == tuple(
        (index * 2 * MIB, MIB) for index in range(9)
    )
    assert sample_ranges(8 * MIB + 1, "edges-2x4m") == ((0, 4 * MIB), (4 * MIB + 1, 4 * MIB))
    with pytest.raises(ValueError):
        sample_ranges(-1, "edges-2x4m")


@pytest.mark.parametrize("candidate", CANDIDATES)
def test_reads_and_digest_bind_sample_bytes_and_size(tmp_path, candidate):
    path = tmp_path / "synthetic.bin"
    write_synthetic(path, 20 * MIB)
    original, bytes_read = hash_candidate(path, candidate)
    assert bytes_read == sum(length for _, length in sample_ranges(20 * MIB, candidate))
    assert hash_candidate(path, candidate)[0] == original
    with path.open("r+b") as stream:
        stream.write(b"changed")
    assert hash_candidate(path, candidate)[0] != original
    write_synthetic(path, 20 * MIB + 1)
    assert hash_candidate(path, candidate)[0] != original


def test_unsampled_change_is_not_whole_file_equality(tmp_path):
    path = tmp_path / "synthetic.bin"
    write_synthetic(path, 20 * MIB)
    original = hash_candidate(path, "edges-2x4m")[0]
    full_original = hash_candidate(path, "full")[0]
    with path.open("r+b") as stream:
        stream.seek(10 * MIB)
        stream.write(b"changed")
    assert hash_candidate(path, "edges-2x4m")[0] == original
    assert hash_candidate(path, "full")[0] != full_original


def test_full_is_standard_sha256_and_report_is_synthetic(tmp_path):
    path = tmp_path / "synthetic.bin"
    write_synthetic(path, 123)
    assert hash_candidate(path, "full") == (hashlib.sha256(path.read_bytes()).hexdigest(), 123)
    report = benchmark([123], 2)
    assert report["production_protocol"] is False
    assert len(report["records"]) == 4
    with pytest.raises(ValueError):
        benchmark([1], 1)
