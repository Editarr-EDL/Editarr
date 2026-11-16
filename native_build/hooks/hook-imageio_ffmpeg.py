"""Use Editarr's inventoried helper instead of collecting a second FFmpeg binary.

The companion runtime hook sets IMAGEIO_FFMPEG_EXE before dependency imports.
The binaries namespace remains importable for imageio-ffmpeg resource lookup.
"""

hiddenimports = ["imageio_ffmpeg.binaries"]
datas = []
