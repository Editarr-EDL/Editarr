#!/usr/bin/env bash
# Build the baseline Linux media libraries from the release's source archive.
set -euo pipefail

sources=$(cd "${1:?provide the unpacked media source directory}" && pwd)
output=${2:?provide a new build directory}
mkdir -p "$output"
output=$(cd "$output" && pwd)
prefix="$output/prefix"
jobs=${EDITARR_BUILD_JOBS:-4}
numpy_headers=$("${EDITARR_BUILD_PYTHON:?provide the build Python}" -c 'import numpy; print(numpy.get_include())')
export PATH="$prefix/bin:$PATH"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"
export LD_LIBRARY_PATH="$prefix/lib"

(cd "$sources" && sha256sum --strict -c MEDIA-SHA256SUMS.txt)
mkdir "$output/src"
cd "$output/src"
for archive in nasm-2.16.03.tar.xz pkgconf-2.5.1.tar.xz x264-b35605a.tar.gz lame-4.0.tar.gz \
    ffmpeg-8.1.3.tar.xz opencv_python_headless-5.0.0.93.tar.gz; do
    tar xf "$sources/$archive"
done

build() (
    cd "$1"
    shift
    ./configure --prefix="$prefix" "$@"
    make -j "$jobs"
    make install
)

build nasm-2.16.03
build pkgconf-2.5.1 --disable-shared
ln -s pkgconf "$prefix/bin/pkg-config"
build x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55 --disable-cli --enable-static --enable-pic
build lame-4.0 --disable-shared --disable-frontend --disable-decoder --with-pic

# PyAV uses the shared libraries. The helper binaries are built separately so
# they remain relocatable when run by themselves, including outside PyInstaller.
ffmpeg_flags=(--disable-autodetect --disable-doc --disable-debug --disable-ffplay
              --enable-gpl --enable-libx264 --enable-libmp3lame
              "--extra-cflags=-I$prefix/include" "--extra-ldflags=-L$prefix/lib")
mkdir ffmpeg-shared ffmpeg-helpers "$output/helpers"
(
    cd ffmpeg-shared
    ../ffmpeg-8.1.3/configure --prefix="$prefix" --enable-shared --disable-static \
        --disable-programs "${ffmpeg_flags[@]}"
    make -j "$jobs"
    make install
)
(
    cd ffmpeg-helpers
    ../ffmpeg-8.1.3/configure --prefix="$prefix" --enable-static --disable-shared "${ffmpeg_flags[@]}"
    make -j "$jobs" ffmpeg ffprobe
    mv ffmpeg ffprobe "$output/helpers/"
)

uv build --wheel --python "$EDITARR_BUILD_PYTHON" --out-dir "$output/wheels" "$sources/av-18.1.0.tar.gz"

opencv="$output/src/opencv_python_headless-5.0.0.93"
patch -d "$opencv" -p1 < "$sources/opencv-optional-typing.patch"
export ENABLE_HEADLESS=1 CMAKE_BUILD_PARALLEL_LEVEL="$jobs"
export CMAKE_ARGS="-DPYTHON3_NUMPY_INCLUDE_DIRS=$numpy_headers
-DBUILD_LIST=core,imgproc,imgcodecs,videoio,dnn,python3 -DWITH_FFMPEG=OFF
-DWITH_IPP=OFF -DWITH_OPENCL=OFF -DWITH_OPENEXR=OFF -DWITH_UNIFONT=OFF
-DWITH_KLEIDICV=OFF -DWITH_ITT=OFF -DBUILD_TESTS=OFF
-DBUILD_PERF_TESTS=OFF -DBUILD_EXAMPLES=OFF"
uv build --wheel --python "$EDITARR_BUILD_PYTHON" --out-dir "$output/wheels" "$opencv"
