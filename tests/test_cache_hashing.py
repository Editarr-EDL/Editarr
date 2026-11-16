import hashlib

from scanner.pipeline.cache import compute_video_md5


def test_compute_video_md5_hashes_entire_small_file(tmp_path):
    payload = (b"small-file-data" * 1024) + b"end"
    video_path = tmp_path / "small.bin"
    video_path.write_bytes(payload)

    expected = hashlib.md5(payload, usedforsecurity=False).hexdigest()

    assert compute_video_md5(str(video_path)) == expected


def test_compute_video_md5_hashes_first_and_last_chunks_for_large_file(tmp_path):
    chunk_size = 10 * 1024 * 1024
    first_chunk = b"A" * chunk_size
    middle_chunk = b"M" * (31 * 1024 * 1024)
    last_chunk = b"Z" * chunk_size

    video_path = tmp_path / "large.bin"
    with video_path.open("wb") as file_obj:
        file_obj.write(first_chunk)
        file_obj.write(middle_chunk)
        file_obj.write(last_chunk)

    expected = hashlib.md5(usedforsecurity=False)
    expected.update(first_chunk)
    expected.update(last_chunk)

    assert compute_video_md5(str(video_path)) == expected.hexdigest()
