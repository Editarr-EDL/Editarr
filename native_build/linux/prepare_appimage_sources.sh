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
    (cd "$sources" && printf '%s  %s\n' "$digest" "$filename" | sha256sum --check --strict)
}

runtime_commit=dd6cebedcbddde9c82f89b011e8e1d40b6e43868
runtime_archive=appimage-type2-runtime-dd6cebe.tar.gz
download "https://codeload.github.com/AppImage/type2-runtime/tar.gz/$runtime_commit" \
    "$runtime_archive" f5fec23be76e50e2445ed2d018bac49b367490fc483c62c4637e99ec705d27ba
download https://github.com/libfuse/libfuse/releases/download/fuse-3.15.0/fuse-3.15.0.tar.xz \
    fuse-3.15.0.tar.xz 70589cfd5e1cff7ccd6ac91c86c01be340b227285c5e200baa284e401eea2ca0
download https://github.com/vasi/squashfuse/archive/0.5.2.tar.gz \
    squashfuse-0.5.2.tar.gz db0238c5981dabbd80ee09ae15387f390091668ca060a7bc38047912491443d3
tar xf "$sources/$runtime_archive" -O "type2-runtime-$runtime_commit/LICENSE" > "$notices/type2-runtime-LICENSE.txt"
tar xf "$sources/fuse-3.15.0.tar.xz" -O fuse-3.15.0/LGPL2.txt > "$notices/libfuse-LGPL2.txt"
tar xf "$sources/squashfuse-0.5.2.tar.gz" -O squashfuse-0.5.2/LICENSE > "$notices/squashfuse-LICENSE.txt"
cat >> "$bootstrap/notices/THIRD-PARTY-NOTICES.md" <<'NOTICE'

The AppImage includes AppImage type-2 runtime 20251108. The source archive
contains that runtime, squashfuse 0.5.2 and libfuse 3.15.0. The runtime source
includes its build recipe and its patch to libfuse for this package format.
NOTICE
