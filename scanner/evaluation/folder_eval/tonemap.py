"""HDR (PQ/HLG) to BT.709 SDR tone-mapping paths for frame extraction.

The path depends on the platform and the filters this ffmpeg build has: VideoToolbox on
macOS, else software tone mapping with zscale + tonemap (zscale linearises the input,
which tonemap requires), else libplacebo. Without any of them HDR items fail instead of
producing washed-out, untone-mapped frames.
"""

from __future__ import annotations

import subprocess
import sys
from functools import lru_cache

VIDEOTOOLBOX = "videotoolbox"
ZSCALE = "zscale"
LIBPLACEBO = "libplacebo"
NO_HDR_PATH = (
    "HDR source but this ffmpeg has no tone-mapping path (needs VideoToolbox scale_vt on "
    "macOS, zscale and tonemap, or libplacebo)"
)


@lru_cache(maxsize=1)
def ffmpeg_filters() -> frozenset[str]:
    """Names from `ffmpeg -filters`, read once per process; empty if ffmpeg fails."""
    try:
        listing = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    names = set()
    for line in listing.splitlines():
        parts = line.split()
        # Filter rows look like " TS zscale  V->V  description".
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return frozenset(names)


def select_hdr_path(platform: str | None = None) -> str | None:
    """Return the tone-mapping path for this platform and ffmpeg, or None if there is none."""
    platform = sys.platform if platform is None else platform
    filters = ffmpeg_filters()
    if platform == "darwin" and "scale_vt" in filters:
        return VIDEOTOOLBOX
    if {"zscale", "tonemap"} <= filters:
        return ZSCALE
    if "libplacebo" in filters:
        return LIBPLACEBO
    return None


def hdr_options(path: str, scale: str, long_edge: int) -> tuple[list[str], str]:
    """Return (input options, video filter) that decode HDR and tone-map to BT.709 SDR.

    `scale` is the software downscale filter used for SDR sources; the VideoToolbox path
    scales on the GPU instead and assumes landscape sources (films).
    """
    if path == VIDEOTOOLBOX:
        return (
            ["-hwaccel", "videotoolbox", "-hwaccel_output_format", "videotoolbox_vld"],
            f"scale_vt=w='min({long_edge},iw)':h=-2:color_matrix=bt709:"
            "color_primaries=bt709:color_transfer=bt709,hwdownload,format=p010le,"
            "format=yuv420p",
        )
    if path == ZSCALE:
        return [], (
            f"{scale},zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
        )
    if path == LIBPLACEBO:
        return [], (
            "libplacebo=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv:"
            f"tonemapping=auto:format=yuv420p,{scale}"
        )
    raise ValueError(f"Unknown HDR path {path!r}")
