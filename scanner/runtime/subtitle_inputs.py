"""Read the selected subtitle file or extract one authorized embedded stream."""

import subprocess
import tempfile
from pathlib import Path

from scanner.formats.subtitles import MAX_SUBTITLE_BYTES, parse_subtitles
from scanner.integrations.jellyfin_subtitles import JellyfinSubtitle
from scanner.pipeline.subtitle_tracks import SUBTITLE_CONTAINERS

from .runtime_support import resolve_ffmpeg
from .subtitle_sidecars import file_fingerprint, read_local_bytes


def read_subtitle(media: Path, track: JellyfinSubtitle):
    """Obtain timed cues from the admitted source without recoding styled text."""
    if track.path is not None:
        content = read_local_bytes(track.path)
    else:
        before = file_fingerprint(media)
        with tempfile.TemporaryFile() as output:
            subprocess.run(
                [
                    resolve_ffmpeg(),
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    SUBTITLE_CONTAINERS,
                    "-i",
                    str(media),
                    "-map",
                    f"0:{track.stream_index}",
                    "-c:s",
                    track.encoder,
                    "-f",
                    track.container,
                    "-fs",
                    str(MAX_SUBTITLE_BYTES),
                    "pipe:1",
                ],
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=45,
            )
            if output.tell() >= MAX_SUBTITLE_BYTES or file_fingerprint(media) != before:
                raise ValueError("The embedded subtitle exceeds limits or the media changed")
            output.seek(0)
            content = output.read(MAX_SUBTITLE_BYTES)
    document = parse_subtitles(content, track.source.format)
    if not document.cues:
        raise ValueError("This subtitle source does not include any timed text")
    return document
