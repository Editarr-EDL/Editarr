#!/usr/bin/env bash
# Collect the pinned source used by the type-2 runtime embedded in the AppImage.
set -euo pipefail

bootstrap=${1:?provide the prepared source and notices directory}
sources="$bootstrap/sources/appimage"
notices="$bootstrap/notices/appimage"
mkdir -p "$sources" "$notices"

download() {
    local url=$1 filename=$2 digest=$3
    curl --fail --location --retry 3 --silent --show-error --output "$sources/$filename" "$url"
    local actual
    actual=$(sha256sum "$sources/$filename")
    actual=${actual%% *}
    if [[ "$actual" != "$digest" ]]; then
        printf 'SHA-256 mismatch for %s from %s: expected %s, received %s\n' \
            "$filename" "$url" "$digest" "$actual" >&2
        return 1
    fi
    printf '%s: OK\n' "$filename"
}

runtime_commit=dd6cebedcbddde9c82f89b011e8e1d40b6e43868
runtime_archive=appimage-type2-runtime-dd6cebe.tar.gz
download "https://codeload.github.com/AppImage/type2-runtime/tar.gz/$runtime_commit" \
    "$runtime_archive" f5fec23be76e50e2445ed2d018bac49b367490fc483c62c4637e99ec705d27ba
download https://github.com/libfuse/libfuse/releases/download/fuse-3.15.0/fuse-3.15.0.tar.xz \
    fuse-3.15.0.tar.xz 70589cfd5e1cff7ccd6ac91c86c01be340b227285c5e200baa284e401eea2ca0
download https://github.com/vasi/squashfuse/archive/0.5.2.tar.gz \
    squashfuse-0.5.2.tar.gz db0238c5981dabbd80ee09ae15387f390091668ca060a7bc38047912491443d3
download https://musl.libc.org/releases/musl-1.2.5.tar.gz \
    musl-1.2.5.tar.gz a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4
download https://codeload.github.com/facebook/zstd/tar.gz/refs/tags/v1.5.6 \
    zstd-1.5.6.tar.gz 30f35f71c1203369dc979ecde0400ffea93c27391bfd2ac5a9715d2173d92ff7
download https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz \
    zlib-1.3.1.tar.gz 9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23
download https://codeload.github.com/microsoft/mimalloc/tar.gz/refs/tags/v2.1.7 \
    mimalloc-2.1.7.tar.gz 0eed39319f139afde8515010ff59baf24de9e47ea316a315398e8027d198202d
# Alpine stable recipes and patches for runtime dependencies at the upstream release.
aports_commit=590fd1489813473277cf110fd88b1c3888d19367
aports_archive=alpine-aports-590fd14.tar.gz
download "https://codeload.github.com/alpinelinux/aports/tar.gz/$aports_commit" \
    "$aports_archive" 143327809737a2bdb6339c845ba860858aaac6299a211fba07eb03730956aaa7
mkdir -p "$sources/alpine-aports"
tar xf "$sources/$aports_archive" -C "$sources/alpine-aports" --strip-components=1 \
    "aports-$aports_commit/main/musl" "aports-$aports_commit/main/zstd" \
    "aports-$aports_commit/main/zlib" "aports-$aports_commit/community/mimalloc2"
rm "$sources/$aports_archive"
tar xf "$sources/$runtime_archive" -O "type2-runtime-$runtime_commit/LICENSE" > "$notices/type2-runtime-LICENSE.txt"
tar xf "$sources/fuse-3.15.0.tar.xz" -O fuse-3.15.0/LGPL2.txt > "$notices/libfuse-LGPL2.txt"
tar xf "$sources/squashfuse-0.5.2.tar.gz" -O squashfuse-0.5.2/LICENSE > "$notices/squashfuse-LICENSE.txt"
tar xf "$sources/musl-1.2.5.tar.gz" -O musl-1.2.5/COPYRIGHT > "$notices/musl-COPYRIGHT.txt"
tar xf "$sources/zstd-1.5.6.tar.gz" -O zstd-1.5.6/LICENSE > "$notices/zstd-LICENSE.txt"
tar xf "$sources/zlib-1.3.1.tar.gz" -O zlib-1.3.1/LICENSE > "$notices/zlib-LICENSE.txt"
tar xf "$sources/mimalloc-2.1.7.tar.gz" -O mimalloc-2.1.7/LICENSE > "$notices/mimalloc-LICENSE.txt"
(
    sources="$bootstrap/sources"
    download https://www.nasm.us/pub/nasm/releasebuilds/2.16.03/nasm-2.16.03.tar.xz \
        nasm-2.16.03.tar.xz 1412a1c760bbd05db026b6c0d1657affd6631cd0a63cddb6f73cc6d4aa616148
)
cat >> "$bootstrap/notices/THIRD-PARTY-NOTICES.md" <<'NOTICE'

The AppImage includes AppImage type-2 runtime 20251108. The source archive
contains that runtime, its build recipe and libfuse patch, and the sources
for libfuse, squashfuse, musl, zstd, zlib, and mimalloc.
NOTICE
