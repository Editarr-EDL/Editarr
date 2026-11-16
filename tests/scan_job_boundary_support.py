"""Synthetic media and detector boundary used by scan authorization regressions."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import imageio_ffmpeg
import pytest

from scanner import worker
from scanner.models import FindingCreate
from scanner.pipeline import runner
from tests.multi_user_access_support import USER
from tests.test_scan_job_authority import _reserved_worker


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("scan-boundary-media") / "synthetic.mkv"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=8:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return path


def prepare_worker(app, monkeypatch, synthetic_video):
    scan_id, _ = app.seed_scan(USER, "boundary-scan")
    Path(app.scan(scan_id).resolved_local_path).write_bytes(synthetic_video.read_bytes())
    _reserved_worker(app, monkeypatch, scan_id)
    monkeypatch.setattr(runner, "require_available", lambda *_args: None)
    monkeypatch.setattr(
        runner,
        "qwen_enrichment_capability",
        lambda: SimpleNamespace(
            availability="unavailable", enrichment_behavior="detector_findings_only"
        ),
    )
    monkeypatch.setattr(runner, "_test_fake_models_enabled", lambda: False)
    monkeypatch.setenv("EDITARR_WORKER_MODEL_DOWNLOADS", "allowed")
    return scan_id


def finding(scan_id):
    return FindingCreate(
        video_id=scan_id,
        category="language",
        subcategory="swearing",
        start_seconds=0,
        end_seconds=0.5,
        severity="medium",
        confidence=1.0,
        label="Synthetic finding",
        source="visual",
    )


def worker_child():
    """Run the real worker with only its external detector replaced."""
    scan_id, token, output = sys.argv[1:]
    runner.require_available = lambda *_args: None
    runner.qwen_enrichment_capability = lambda: SimpleNamespace(
        availability="unavailable", enrichment_behavior="detector_findings_only"
    )

    def detector(*_args, **_kwargs):
        print("DETECTOR_READY", flush=True)
        if sys.stdin.readline().strip() != "continue":
            raise RuntimeError("Missing test release")
        if output == "empty":
            return []
        if output == "download":
            from tests.test_model_acquisition import FakeRepository, _service

            service = _service(Path(worker.settings.data_dir) / "synthetic-model", FakeRepository())
            assert service.status("fake-model").state == "not_downloaded"
            service.acquire("fake-model")
        return [finding(scan_id)]

    runner._scan_visual_isolated = detector
    raise SystemExit(worker.run_scan_worker(scan_id, token))


if __name__ == "__main__":
    worker_child()
