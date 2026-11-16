import sys
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from scanner.pipeline import cache
from scanner.pipeline.frames import (
    _playable_video_stream_index,
    _positive_stream_duration,
    _positive_stream_frame_rate,
    get_video_duration,
)


class _Stream:
    def __init__(
        self,
        disposition: int,
        average_rate: Fraction | None,
        base_rate: Fraction | None,
        duration: int | None = None,
        time_base: Fraction | None = None,
    ):
        self.disposition = disposition
        self.average_rate = average_rate
        self.base_rate = base_rate
        self.duration = duration
        self.time_base = time_base


def test_frame_rate_ignores_an_attached_cover_image_before_the_video():
    streams = [
        _Stream(disposition=1 << 10, average_rate=None, base_rate=Fraction(90_000, 1)),
        _Stream(
            disposition=1,
            average_rate=Fraction(24_000, 1_001),
            base_rate=Fraction(24_000, 1_001),
        ),
    ]

    assert _positive_stream_frame_rate(streams) == float(Fraction(24_000, 1_001))


def test_frame_rate_returns_none_when_only_attached_images_are_present():
    streams = [_Stream(disposition=1 << 10, average_rate=None, base_rate=Fraction(90_000, 1))]

    assert _positive_stream_frame_rate(streams) is None


def test_duration_ignores_an_attached_cover_image_before_the_video():
    streams = [
        _Stream(
            disposition=1 << 10,
            average_rate=None,
            base_rate=Fraction(90_000, 1),
            duration=9_106_500,
            time_base=Fraction(1, 90_000),
        ),
        _Stream(
            disposition=1,
            average_rate=Fraction(24_000, 1_001),
            base_rate=Fraction(24_000, 1_001),
            duration=2_426_400,
            time_base=Fraction(1, 24_000),
        ),
    ]

    assert _positive_stream_duration(streams) == 101.1


def test_duration_uses_container_metadata_before_counting_every_frame(monkeypatch):
    class _Container:
        duration = 5_707_456_000
        streams = SimpleNamespace(
            video=[
                _Stream(
                    disposition=1,
                    average_rate=Fraction(24_000, 1_001),
                    base_rate=Fraction(24_000, 1_001),
                )
            ]
        )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "av",
        SimpleNamespace(open=lambda _path: _Container(), time_base=1_000_000),
    )

    def fail_if_counted(_path):
        raise AssertionError("duration metadata should avoid decoding every frame")

    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(count_frames_and_secs=fail_if_counted),
    )

    assert get_video_duration("movie.mkv") == 5707.456


def test_playable_stream_index_ignores_an_attached_cover_image():
    streams = [
        _Stream(disposition=1 << 10, average_rate=None, base_rate=Fraction(90_000, 1)),
        _Stream(disposition=1, average_rate=Fraction(24, 1), base_rate=Fraction(24, 1)),
    ]
    streams[0].index = 0
    streams[1].index = 1

    assert _playable_video_stream_index(streams) == 1


def test_scene_cache_is_saved_with_the_expected_filename(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cache.settings, "cache_dir", tmp_path / "cache")
    legacy_path = tmp_path / "cache" / "videos" / "video-hash" / "scenes_threshold_27_0.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text("[]")

    assert cache.load_cached_scenes("video-hash", 27.0) == []

    cache.save_cached_scenes("video-hash", 27.0, [])

    assert legacy_path.is_file()


def test_credits_result_cache_requires_a_versioned_sha256_key(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cache.settings, "cache_dir", tmp_path / "cache")
    cache_key = "a" * 64

    assert cache.load_cached_credits_result(cache_key) is None
    cache.save_cached_credits_result(cache_key, {"outcome": "no_candidate"})
    assert cache.load_cached_credits_result(cache_key) == {"outcome": "no_candidate"}

    try:
        cache.load_cached_credits_result("../outside")
    except ValueError as exc:
        assert str(exc) == "Credits cache key must be a SHA-256 digest"
    else:
        raise AssertionError("Invalid cache key was accepted")
