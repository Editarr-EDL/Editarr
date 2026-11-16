import json
from pathlib import Path

from scanner.evaluation import video_model_corpus


def test_add_labeled_range_extracts_frames_and_appends_manifest(monkeypatch, tmp_path: Path):
    video_path = tmp_path / "video.mkv"
    video_path.write_bytes(b"video")

    def extract(video: str, scene, output_dir: Path, *, interval_seconds: float):
        assert video == str(video_path)
        assert (scene.start_seconds, scene.end_seconds, interval_seconds) == (10.0, 12.0, 1.0)
        output_dir.mkdir(parents=True)
        frames = [(output_dir / "scene_0000_000001.jpg", 10.0),
                  (output_dir / "scene_0000_000002.jpg", 11.0)]
        for frame_path, _timestamp in frames:
            frame_path.write_bytes(b"frame")
        return frames

    monkeypatch.setattr(video_model_corpus, "sample_scene_frames", extract)
    corpus_dir = tmp_path / "corpus"

    result = video_model_corpus.add_labeled_range(
        video_path,
        corpus_dir,
        start_seconds=10,
        end_seconds=12,
        categories="violence,nudity",
    )

    assert result == {
        "added_frames": 2,
        "manifest": str(corpus_dir / "manifest.json"),
        "capture_dir": str(corpus_dir / "frames" / "capture_0001"),
        "expected_categories": ["violence", "nudity"],
    }
    manifest = json.loads((corpus_dir / "manifest.json").read_text())
    assert manifest["frames"] == [
        {
            "path": "frames/capture_0001/scene_0000_000001.jpg",
            "expected_categories": ["violence", "nudity"],
            "timestamp_seconds": 10.0,
            "source_video": str(video_path),
        },
        {
            "path": "frames/capture_0001/scene_0000_000002.jpg",
            "expected_categories": ["violence", "nudity"],
            "timestamp_seconds": 11.0,
            "source_video": str(video_path),
        },
    ]


def test_add_labeled_range_rejects_unknown_category(tmp_path: Path):
    video_path = tmp_path / "video.mkv"
    video_path.write_bytes(b"video")

    try:
        video_model_corpus.add_labeled_range(
            video_path,
            tmp_path / "corpus",
            start_seconds=0,
            end_seconds=1,
            categories="weapons",
        )
    except ValueError as exc:
        assert "Invalid category" in str(exc)
    else:
        raise AssertionError("Expected an unknown corpus category to be rejected")
