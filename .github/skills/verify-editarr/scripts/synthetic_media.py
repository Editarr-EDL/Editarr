"""Generate Editarr's deterministic synthetic media fixture through app boundaries.

Usage: PYTHONPATH=CHECKOUT .venv/bin/python -P synthetic_media.py OUTPUT_DIRECTORY

FFmpeg comes from the app's resolve_ffmpeg and tracks from its probe_audio_tracks.
Prints one JSON record. Reruns replace the file atomically with identical bytes
for the same FFmpeg build, so the SHA-256 is the stable synthetic media version.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from scanner.pipeline.audio_tracks import probe_audio_tracks
from scanner.runtime.runtime_support import resolve_ffmpeg

FIXTURE = "synthetic-av-v1"
DURATION_SECONDS = 4
TRACKS = (("eng", "Main", 440), ("spa", "Commentary", 880))
VIDEO = f"testsrc2=size=320x180:rate=24:duration={DURATION_SECONDS}"
ENCODE = "-c:v mpeg4 -q:v 5 -c:a aac -threads 1 -map_metadata -1".split()
DETERMINISTIC = "-fflags +bitexact -flags +bitexact".split()


def _command(ffmpeg: str, target: Path) -> list[str]:
    command = [ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", VIDEO]
    for _, _, frequency in TRACKS:
        tone = f"sine=frequency={frequency}:sample_rate=48000:duration={DURATION_SECONDS}"
        command += ["-f", "lavfi", "-i", tone]
    command += ["-map", "0:v"]
    for index, (language, title, _) in enumerate(TRACKS):
        command += ["-map", f"{index + 1}:a"]
        command += [f"-metadata:s:a:{index}", f"language={language}"]
        command += [f"-metadata:s:a:{index}", f"title={title}"]
    disposition = ["-disposition:a:0", "default", "-disposition:a:1", "0"]
    return [*command, *ENCODE, *DETERMINISTIC, *disposition, "-f", "matroska", str(target)]


def generate(directory: Path) -> dict[str, object]:
    """Create or replace the fixture in `directory` and describe it as Editarr probes it."""
    ffmpeg = resolve_ffmpeg()
    target = directory / f"{FIXTURE}.mkv"
    partial = directory / f".{FIXTURE}.partial"
    subprocess.run(_command(ffmpeg, partial), check=True, capture_output=True, timeout=60)
    os.replace(partial, target)
    inventory = probe_audio_tracks(target)
    version = subprocess.run(
        [ffmpeg, "-version"], check=True, capture_output=True, text=True, timeout=10
    )
    return {
        "fixture": FIXTURE,
        "file": str(target),
        "bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "ffmpeg": ffmpeg,
        "ffmpegVersion": version.stdout.splitlines()[0],
        "durationSeconds": DURATION_SECONDS,
        "preselectedStreamIndex": inventory.preselected_stream_index,
        "tracks": [
            {
                "streamIndex": track.stream_index,
                "language": track.language,
                "title": track.title,
                "codec": track.codec,
                "channels": track.channels,
            }
            for track in inventory.tracks
        ],
    }


if __name__ == "__main__":
    print(json.dumps(generate(Path(sys.argv[1])), indent=2))
