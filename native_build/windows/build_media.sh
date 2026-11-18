#!/usr/bin/env bash
# Build the media libraries and relocatable tools under MSYS2 UCRT64.
set -euo pipefail

sources=$(cd "$(cygpath -u "${1:?provide the checked media sources}")" && pwd)
output=$(cygpath -u "${2:?provide a new media output directory}")
mkdir "$output"
prefix="$output/prefix"
jobs=${EDITARR_BUILD_JOBS:-3}
export PKG_CONFIG_PATH="$prefix/lib/pkgconfig"

(cd "$sources" && sha256sum -c MEDIA-SHA256SUMS.txt)
mkdir "$output/src"
cd "$output/src"
for archive in x264-b35605a.tar.gz lame-4.0.tar.gz ffmpeg-8.1.3.tar.xz; do
    tar xf "$sources/$archive"
done

build() (
    cd "$1"
    shift
    ./configure --prefix="$prefix" "$@"
    make -j "$jobs"
    make install
)

build x264-b35605ace3ddf7c1a5d67a2eb553f034aef41d55 \
    --disable-cli --disable-opencl --enable-static
ac_cv_prog_cc_c23=no CFLAGS='-O2 -Wno-implicit-function-declaration' \
    build lame-4.0 --disable-shared --disable-frontend --disable-decoder

ffmpeg="$output/src/ffmpeg-8.1.3"
ffmpeg_options=(
    --target-os=mingw32 --arch=x86_64
    --disable-autodetect --disable-doc --disable-debug --disable-ffplay
    --disable-pthreads --enable-w32threads
    --enable-gpl --enable-libx264 --enable-libmp3lame
    "--extra-cflags=-I$prefix/include"
)
mkdir ffmpeg-tools ffmpeg-pyav
(
    cd ffmpeg-tools
    "$ffmpeg/configure" --prefix="$output/standalone" "${ffmpeg_options[@]}" \
        --enable-static --disable-shared "--extra-ldflags=-L$prefix/lib -static"
    make -j "$jobs"
    make install
)
(
    cd ffmpeg-pyav
    "$ffmpeg/configure" --prefix="$prefix" "${ffmpeg_options[@]}" \
        --disable-programs --enable-shared --disable-static \
        "--extra-ldflags=-L$prefix/lib -static-libgcc"
    make -j "$jobs"
    make install
)

for program in ffmpeg ffprobe; do
    "$output/standalone/bin/$program.exe" -version
done
