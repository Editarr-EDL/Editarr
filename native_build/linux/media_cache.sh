#!/usr/bin/env bash
# Verify or write a manifest for the cached Linux media build.
set -euo pipefail

operation=${1:?expected verify or record}
media=${2:?provide the Linux media build directory}
export LC_ALL=C
test -d "$media"
cd "$media"
test -x helpers/ffmpeg
test -x helpers/ffprobe
shopt -s nullglob
av=(wheels/av-*.whl)
opencv=(wheels/opencv_python_headless-*.whl)
test "${#av[@]}" -eq 1
test "${#opencv[@]}" -eq 1
for library in avcodec avdevice avfilter avformat avutil swresample swscale; do
    test -f "prefix/lib/lib$library.so"
done
files=(helpers/ffmpeg helpers/ffprobe prefix/lib/*.so* "${av[@]}" "${opencv[@]}")
case "$operation" in
    record) sha256sum "${files[@]}" > SHA256SUMS.txt ;;
    verify) test -f SHA256SUMS.txt && sha256sum "${files[@]}" | cmp -s - SHA256SUMS.txt ;;
    *) exit 2 ;;
esac
