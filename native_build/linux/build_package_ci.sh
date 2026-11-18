#!/usr/bin/env bash
# Run the inherited Linux package checks on the release's glibc 2.28 baseline.
set -euo pipefail

output=${1:?provide the mounted output directory}
test "$(uname -m)" = x86_64
test "$(getconf GNU_LIBC_VERSION)" = "glibc 2.28"
dnf install -y python3.11 python3.11-pip git curl tar xz file binutils \
    desktop-file-utils libappstream-glib util-linux procps-ng jq findutils \
    gcc-toolset-14-gcc gcc-toolset-14-gcc-c++ make patch diffutils gzip
dnf install -y epel-release
dnf install -y nasm

export HOME="$output/home" TMPDIR="$output/tmp" UV_CACHE_DIR="$output/uv-cache"
export UV_PROJECT_ENVIRONMENT="$output/venv" UV_PYTHON_INSTALL_DIR="$output/python"
export UV_PYTHON=3.11.16 SOURCE_DATE_EPOCH=315532800
export NPM_CONFIG_PREFIX="$output/node-packages"
export PATH="$NPM_CONFIG_PREFIX/bin:$PATH"
export GOTOOLCHAIN=local CGO_ENABLED=0
mkdir -p "$HOME" "$TMPDIR" "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR"
# CI sets this repository up outside the container, under the runner user's UID.
git config --global --add safe.directory "$PWD"
set +u
source /opt/rh/gcc-toolset-14/enable
set -u

python3.11 -m pip install --disable-pip-version-check uv==0.12.5
npm install --global --no-audit --no-fund pnpm@11.22.0
uv sync --locked --managed-python --extra dev
if [[ -z ${VERSION:-} ]]; then
    uv run --no-sync python -m pytest -q -n 0 \
        tests/test_audio_progress.py tests/test_cli_admin.py tests/test_cli_server.py \
        tests/test_installed_artifact_verifier.py tests/test_linux_package.py \
        tests/test_linux_model_verifier.py tests/test_model_capabilities.py \
        tests/test_native_runtime_build.py tests/test_native_artifact_size.py \
        tests/test_native_collection_size.py --junitxml "$output/native14-linux-junit.xml"
fi

export EDITARR_BUILD_PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python" UV_NO_SYNC=1
bash native_build/linux/build_media.sh "$output/bootstrap/sources" "$output/media"
media="$output/media"
uv pip uninstall opencv-python opencv-python-headless
uv pip install --no-deps --reinstall "$media"/wheels/*.whl
export LD_LIBRARY_PATH="$media/prefix/lib"
uv run python -c 'import av; from cv2.dnn import blobFromImage'

read -r appimagetool_release appimage_runtime_release < <(uv run python -c \
    'from scripts.package_linux import APPIMAGETOOL_RELEASE, APPIMAGE_RUNTIME_RELEASE; print(APPIMAGETOOL_RELEASE, APPIMAGE_RUNTIME_RELEASE)')
curl --fail --location --retry 3 --output "$output/appimagetool" \
    "https://github.com/AppImage/appimagetool/releases/download/$appimagetool_release/appimagetool-x86_64.AppImage"
curl --fail --location --retry 3 --output "$output/runtime-x86_64" \
    "https://github.com/AppImage/type2-runtime/releases/download/$appimage_runtime_release/runtime-x86_64"
chmod 755 "$output/appimagetool" "$output/runtime-x86_64"

verification_flags=()
if [[ -z ${VERSION:-} ]]; then
    verification_flags+=(--test-fake-models)
fi
runtime="$output/raw output/Editarr"
uv run python -m scripts.build_release --target linux-x86_64 --source-media-release \
    --out-dir "$output/raw output" --work-dir "$TMPDIR" \
    --ffmpeg-helper "$media/helpers/ffmpeg" --ffprobe-helper "$media/helpers/ffprobe" \
    "${verification_flags[@]}"
unset LD_LIBRARY_PATH
if [[ -n ${VERSION:-} ]]; then
    jq --exit-status --arg version "$VERSION" --arg revision "$(git rev-parse HEAD)" \
        '.version == $version and .source_revision == $revision and .source_dirty == false and .target == "linux-x86_64"' \
        "$runtime/editarr-runtime-inventory.json" >/dev/null
    test -f "$runtime/_internal/source-media-release.marker"
    test ! -e "$runtime/_internal/native-test-fake-models.marker"
fi
for helper in ffmpeg ffprobe; do
    "$runtime/_internal/helpers/$helper" -version
done
uv run python -m scripts.verify_installed_release --runtime "$runtime" --target linux-x86_64 \
    "${verification_flags[@]}" --json-out "$output/native14-baseline-raw-verifier.json"

package="$output/package output"
uv run python -m scripts.package_linux --runtime "$runtime" --out-dir "$package" \
    --work-dir "$TMPDIR" --appimagetool "$output/appimagetool" \
    --appimage-runtime "$output/runtime-x86_64"
rm -rf "$output/raw output"
packaged_runtime="$package/Editarr.AppDir/usr/lib/editarr"
uv run python -m scripts.verify_installed_release --runtime "$packaged_runtime" --target linux-x86_64 \
    "${verification_flags[@]}" --json-out "$output/native14-baseline-appdir-verifier.json"

appimage="$package/$(jq -er .appimage "$package/editarr-linux-package.json")"
baseline_xdg="$output/baseline-xdg"
mkdir -p "$baseline_xdg"/{home,data,config,state,cache,runtime,tmp}
chmod 700 "$baseline_xdg/runtime"
(
    cd "$TMPDIR"
    env HOME="$baseline_xdg/home" TMPDIR="$baseline_xdg/tmp" \
        XDG_DATA_HOME="$baseline_xdg/data" XDG_CONFIG_HOME="$baseline_xdg/config" \
        XDG_STATE_HOME="$baseline_xdg/state" XDG_CACHE_HOME="$baseline_xdg/cache" \
        XDG_RUNTIME_DIR="$baseline_xdg/runtime" "$appimage" --appimage-extract-and-run paths \
        > "$output/native14-baseline-extract-paths.txt"
)
grep -F "$baseline_xdg/data/editarr" "$output/native14-baseline-extract-paths.txt"

if [[ -z ${VERSION:-} ]]; then
    touch "$output/native14-baseline-real-models.json"
    chown nobody "$output/native14-baseline-real-models.json"
    runuser -u nobody -- env TMPDIR=/tmp "$UV_PROJECT_ENVIRONMENT/bin/python" \
        -m scripts.verify_linux_models --runtime "$packaged_runtime" --appimage "$appimage" \
        --mode extract --json-out "$output/native14-baseline-real-models.json"
else
    uv run python -m scripts.prepare_alpha_distribution package linux-x86_64 \
        "$output/bootstrap" "$appimage" "$output/release-assets"
fi
git diff --check
