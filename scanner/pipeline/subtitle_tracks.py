"""Probe embedded subtitles by local physical stream index, not Jellyfin's logical index."""

from pathlib import Path

from .audio_tracks import MAX_STREAMS, PROBE_OPTIONS
from .bounded_media import BoundedInput, MediaBudgetError

SUBTITLE_CONTAINERS = "matroska,webm,mov,mp4,m4a,3gp,3g2,mj2,avi,mpeg,mpegts,flv,asf,ogg"


def embedded_subtitle_streams(media: Path) -> list[dict]:
    """Return file-local codecs, indexes, and dispositions under the normal probe budget."""
    import av

    options = {**PROBE_OPTIONS, "format_whitelist": SUBTITLE_CONTAINERS}
    # FFmpeg AV_DISPOSITION_FORCED and AV_DISPOSITION_HEARING_IMPAIRED.
    try:
        with BoundedInput(media) as source:
            with av.open(source, options=options, timeout=5.0) as container:
                if len(container.streams) > MAX_STREAMS:
                    return []
                return [
                    {
                        "Index": stream.index,
                        "Codec": stream.codec_context.name,
                        "Language": stream.metadata.get("language"),
                        "Title": stream.metadata.get("title"),
                        "IsForced": bool(int(stream.disposition) & 0x40),
                        "IsHearingImpaired": bool(int(stream.disposition) & 0x80),
                    }
                    for stream in container.streams.subtitles
                    if stream.codec_context is not None
                ]
    except (OSError, MediaBudgetError, av.error.FFmpegError):
        return []
