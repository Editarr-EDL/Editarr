"""Point imageio-ffmpeg and transitive consumers at the single frozen helper."""

import os
import sys
from pathlib import Path


def _configure_ffmpeg() -> None:
    # Match the application's existing explicit-override policy.
    if os.environ.get("IMAGEIO_FFMPEG_EXE", "").strip():
        return
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    helper = Path(sys._MEIPASS) / "helpers" / name
    if not helper.is_file():
        raise RuntimeError("Frozen Editarr FFmpeg helper is missing")
    os.environ["IMAGEIO_FFMPEG_EXE"] = str(helper)


_configure_ffmpeg()
