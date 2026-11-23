"""The legacy MD5 byte budget uses a stable path-scoped observation."""

from hashlib import md5

import pytest

from scanner.integrations.community.fingerprints import FileChangedError
from scanner.runtime import media_fingerprints as fingerprints

_MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def private_witness_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(fingerprints.settings, "data_dir", tmp_path / "data")


@pytest.mark.parametrize("size", [0, 3 * _MIB, 50 * _MIB, 50 * _MIB + 1])
def test_legacy_sample_matches_previous_byte_ranges_and_progress(tmp_path, size):
    media = tmp_path / "movie.mkv"
    with media.open("wb") as stream:
        stream.truncate(size)
        if size:
            stream.write(b"start")
            stream.seek(size // 2)
            stream.write(b"middle")
            stream.seek(size - 3)
            stream.write(b"end")
    progress = []
    sample = fingerprints.sampled_media_fingerprint(
        media, on_progress=lambda done, total: progress.append((done, total))
    )
    data = media.read_bytes()
    expected = data[: 10 * _MIB] + data[-10 * _MIB :] if size > 50 * _MIB else data
    assert sample.path == media.resolve()
    assert sample.size_bytes == size
    assert sample.digest == md5(expected, usedforsecurity=False).hexdigest()
    assert fingerprints.sampled_media_read_size(size) == len(expected)
    assert progress[0] == (0, len(expected))
    assert progress[-1] == (len(expected), len(expected))
    assert fingerprints.cached_sampled_media_fingerprint(media) == sample
    assert fingerprints.cached_content_digest(media) is None


def test_changed_generation_invalidates_sample_witness_even_with_same_edges(tmp_path):
    media = tmp_path / "movie.mkv"
    replacement = tmp_path / "replacement.mkv"
    for path, middle in ((media, b"a"), (replacement, b"b")):
        with path.open("wb") as stream:
            stream.truncate(60 * _MIB)
            stream.seek(30 * _MIB)
            stream.write(middle)
    original = fingerprints.sampled_media_fingerprint(media)
    replacement.replace(media)
    assert fingerprints.cached_sampled_media_fingerprint(media) is None
    current = fingerprints.sampled_media_fingerprint(media)
    assert original.digest == current.digest
    assert original.generation != current.generation


@pytest.mark.parametrize("alias", [False, True])
def test_same_bytes_replacement_while_sampling_discards_the_observation(tmp_path, alias):
    media = tmp_path / "movie.mkv"
    other = tmp_path / "other.mkv"
    for path in (media, other):
        path.write_bytes(b"original media")
    target = media
    if alias:
        target = tmp_path / "alias.mkv"
        try:
            target.symlink_to(media)
        except OSError:
            pytest.skip("filesystem cannot create symlinks")

    def replace_on_first_read(done, _total):
        if done == 0:
            if alias:
                target.unlink()
                target.symlink_to(other)
            else:
                other.replace(media)

    with pytest.raises(FileChangedError, match="media file changed"):
        fingerprints.sampled_media_fingerprint(target, on_progress=replace_on_first_read)
    assert fingerprints.cached_sampled_media_fingerprint(target) is None
