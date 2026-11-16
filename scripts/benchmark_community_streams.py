"""Run the explicitly approved generated-only CSS-35B candidate experiment."""

import json
import subprocess
import tempfile
from pathlib import Path

import av

from scanner.integrations.community.stream_experiments import SamplingCandidate, sample_stream
from scanner.runtime.runtime_support import resolve_ffmpeg


def main():
    ffmpeg = resolve_ffmpeg()
    report = {
        "pyav": av.__version__,
        "ffmpeg": subprocess.run(
            [ffmpeg, "-version"], check=True, capture_output=True, text=True
        ).stdout.splitlines()[0],
        "results": [],
    }
    with tempfile.TemporaryDirectory(prefix="css35b-generated-") as directory:
        root = Path(directory)

        def generate(name, args):
            output = root / name
            subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", *args, "-y", str(output)],
                check=True,
                capture_output=True,
                timeout=30,
            )
            if (
                output.stat().st_size > 32 * 1024 * 1024
                or sum(file.stat().st_size for file in root.iterdir()) > 256 * 1024 * 1024
            ):
                raise RuntimeError("Approved asset budget exceeded")
            return output

        base = generate(
            "base.mp4",
            [
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=30:duration=12",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=12",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:duration=12",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-map",
                "2:a",
                "-c:v",
                "libx264",
                "-g",
                "30",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-metadata:s:a:0",
                "language=eng",
                "-metadata:s:a:0",
                "title=Main",
                "-metadata:s:a:1",
                "language=eng",
                "-metadata:s:a:1",
                "title=Commentary",
            ],
        )
        variants = {
            "base": base,
            "remux": generate("remux.mov", ["-i", str(base), "-map", "0", "-c", "copy"]),
            "timebase": generate(
                "timebase.mp4",
                ["-i", str(base), "-map", "0", "-c", "copy", "-video_track_timescale", "90000"],
            ),
            "reordered": generate(
                "reordered.mp4",
                ["-i", str(base), "-map", "0:v", "-map", "0:a:1", "-map", "0:a:0", "-c", "copy"],
            ),
            "repacketized": generate(
                "repacketized.ts", ["-i", str(base), "-map", "0", "-c", "copy"]
            ),
            "missing_index": generate(
                "missing-index.mkv", ["-i", str(base), "-map", "0", "-c", "copy"]
            ),
            "changed_audio": generate(
                "changed-audio.mp4",
                [
                    "-i",
                    str(base),
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=660:duration=12",
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-map",
                    "0:a:1",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                ],
            ),
            "changed_video": generate(
                "changed-video.mp4",
                [
                    "-i",
                    str(base),
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=green:size=320x180:rate=30:duration=12",
                    "-map",
                    "1:v",
                    "-map",
                    "0:a",
                    "-c:v",
                    "libx264",
                    "-g",
                    "30",
                    "-c:a",
                    "copy",
                ],
            ),
            "delayed": generate(
                "delayed.mp4",
                [
                    "-i",
                    str(base),
                    "-itsoffset",
                    "0.5",
                    "-i",
                    str(base),
                    "-map",
                    "0:v",
                    "-map",
                    "1:a",
                    "-c",
                    "copy",
                ],
            ),
            "reencode": generate(
                "reencode.mp4",
                [
                    "-i",
                    str(base),
                    "-map",
                    "0",
                    "-c:v",
                    "libx264",
                    "-g",
                    "30",
                    "-crf",
                    "32",
                    "-c:a",
                    "aac",
                ],
            ),
            "unsupported": generate(
                "unsupported.mov", ["-i", str(base), "-map", "0:a:0", "-c:a", "pcm_s16le"]
            ),
        }
        for candidate in (
            SamplingCandidate(3, 0.25),
            SamplingCandidate(5, 0.25),
            SamplingCandidate(3, 0.5),
        ):
            for name, file in variants.items():
                with av.open(str(file)) as container:
                    indexes = [
                        stream.index
                        for stream in container.streams
                        if stream.type in {"video", "audio"}
                    ]
                for index in indexes:
                    try:
                        result = sample_stream(file, index, candidate)
                        result.pop("configuration")
                        result.pop("samples")
                    except (ValueError, OSError) as error:
                        result = {
                            "unsupported": str(error),
                            "candidate": {
                                "positions": candidate.positions,
                                "window_seconds": candidate.window_seconds,
                            },
                        }
                    report["results"].append({"variant": name, "index": index, **result})
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
