#!/usr/bin/env bash
# Build the release's media inputs from the source archives shipped with the DMG.
set -euo pipefail

sources=$(cd "${1:?provide the unpacked media source directory}" && pwd)
output=${2:?provide a new build directory}
mkdir "$output"
output=$(cd "$output" && pwd)
prefix="$output/prefix"
jobs=${EDITARR_BUILD_JOBS:-8}
numpy_headers=$("${EDITARR_BUILD_PYTHON:-.venv/bin/python}" -c 'import numpy; print(numpy.get_include())')
export MACOSX_DEPLOYMENT_TARGET=26.2
export PATH="$prefix/bin:$PATH"
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"

(cd "$sources" && shasum -a 256 -c MEDIA-SHA256SUMS.txt)
mkdir "$output/src"
cd "$output/src"
for archive in pkgconf-2.5.1.tar.xz x264-b35605a.tar.gz lame-4.0.tar.gz \
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

build pkgconf-2.5.1 --disable-shared
ln -s pkgconf "$prefix/bin/pkg-config"
build x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55 --disable-cli --enable-static --enable-pic
ac_cv_prog_cc_c23=no CFLAGS='-O2 -Wno-implicit-function-declaration' \
    build lame-4.0 --disable-shared --disable-frontend --disable-decoder
build ffmpeg-8.1.3 --install-name-dir=@rpath --disable-autodetect --disable-doc \
    --disable-debug --disable-ffplay --enable-shared --disable-static --enable-gpl \
    --enable-libx264 --enable-libmp3lame --enable-videotoolbox --enable-audiotoolbox \
    --extra-cflags="-I$prefix/include" \
    --extra-ldflags="-L$prefix/lib -Wl,-rpath,@loader_path/../lib -Wl,-rpath,$prefix/lib"

CPPFLAGS="-I$prefix/include" CFLAGS="-I$prefix/include" \
    LDFLAGS="-L$prefix/lib -Wl,-rpath,$prefix/lib" \
    uv build --wheel --python 3.11 --out-dir "$output/wheels" "$sources/av-18.1.0.tar.gz"

opencv="$output/src/opencv_python_headless-5.0.0.93"
patch -d "$opencv" -p1 < "$sources/opencv-optional-typing.patch"
export ENABLE_HEADLESS=1 CMAKE_BUILD_PARALLEL_LEVEL="$jobs"
export CMAKE_ARGS="-DPYTHON3_NUMPY_INCLUDE_DIRS=$numpy_headers
-DBUILD_LIST=core,imgproc,imgcodecs,videoio,python3 -DWITH_FFMPEG=OFF
-DWITH_AVFOUNDATION=OFF -DWITH_IPP=OFF -DWITH_OPENCL=OFF -DWITH_OPENEXR=OFF
-DWITH_UNIFONT=OFF -DWITH_KLEIDICV=OFF -DWITH_ITT=OFF -DBUILD_TESTS=OFF
-DBUILD_PERF_TESTS=OFF -DBUILD_EXAMPLES=OFF"
uv build --wheel --python 3.11 --out-dir "$output/wheels" "$opencv"
