#!/usr/bin/env bash
# Run from a clean, versioned Editarr checkout on an Apple Silicon Mac.
set -euo pipefail

output=${1:?usage: build_macos_distribution.sh NEW_OUTPUT_DIRECTORY}
mkdir -p "$output"
output=$(cd "$output" && pwd)
sources="$output/third-party"
notices="$output/notices"
media=${EDITARR_MACOS_MEDIA_DIR:-"$output/media"}
python_version=3.11.16
bootstrap=https://github.com/Editarr-EDL/Editarr/releases/download/v0.1.2
version=$(uv run --no-project --python "$python_version" --managed-python python -c \
  'from pathlib import Path; from scripts.release_assets import validate_release_versions; print(validate_release_versions(Path.cwd()))')
tag="v$version"
revision=$(git rev-parse HEAD)
repo=${GITHUB_REPOSITORY:-Editarr-EDL/Editarr}
[[ $(uname -m) == arm64 && $(uname -s) == Darwin ]]
[[ -z $(git status --porcelain) ]]
mkdir "$sources" "$notices" "$output/assets" "$output/work"

fetch_bootstrap() {
  local suffix=$1 digest=$2 destination=$3
  local filename="Editarr-v0.1.2-macOS-$suffix.tar.gz"
  local archive="$output/$filename"
  if [[ -n ${EDITARR_MACOS_BOOTSTRAP_DIR:-} ]]; then
    cp "$EDITARR_MACOS_BOOTSTRAP_DIR/$filename" "$archive"
  else
    curl --fail --location --retry 3 --output "$archive" "$bootstrap/$filename"
  fi
  printf '%s  %s\n' "$digest" "$archive" | shasum -a 256 -c -
  tar -xzf "$archive" -C "$destination"
  rm "$archive"
}

fetch_bootstrap Third-Party-Source bdf3a2abbbebf2c6c14bba3c4988bc3428ac10f375dcc908c9e002fab35b7d3b "$sources"
fetch_bootstrap Notices 3d6fd17785abdcacf0bacaad754b9a142730d231bfb9268a9760d29167a385fc "$notices"
(cd "$sources" && shasum -a 256 -c MEDIA-SHA256SUMS.txt)
cp native_build/macos/build_media.sh "$sources/build_media.sh"
sed "s/v0\.1\.2/$tag/g" "$sources/SOURCE-README.txt" > "$output/source-readme.txt"
mv "$output/source-readme.txt" "$sources/SOURCE-README.txt"
cp LICENSE "$notices/Editarr-GPLv3.txt"
go_root=$(go env GOROOT)
go_license="$go_root/LICENSE"
if [[ ! -f "$go_license" ]]; then go_license="$go_root/../LICENSE"; fi
cp "$go_license" "$notices/go/Go-runtime/Go-LICENSE"

cat > "$notices/THIRD-PARTY-NOTICES.md" <<EOF
# Editarr $tag for macOS, third-party notices

Editarr is distributed under the GNU General Public License v3.0. The application
source, launcher, build scripts, and lockfiles are at https://github.com/$repo/tree/$tag.
The release at https://github.com/$repo/releases/tag/$tag includes the corresponding
third-party sources named in Editarr-$tag-macOS-Third-Party-Source.tar.gz.

- FFmpeg 8.1.3 and ffprobe are built from source. The source archive includes
  FFmpeg, x264, LAME, the build recipe, and the media input checksums. PyAV uses
  the same FFmpeg libraries. OpenCV uses the codecs from its source distribution.
- SciPy uses Apple Accelerate and includes GCC runtime libraries. The source
  archive includes GCC 13.4.0, the Darwin patch, and the upstream formula.
- The source archive also contains the corresponding sources for CPython,
  PyInstaller, certifi, tqdm, and other packaged components named in its README.
- Optional model weights are downloaded from the pinned upstream sources. See
  docs/model-artifact-notices.md in the tagged source for their license metadata.

The license files in this archive apply to their respective components.
EOF

uv sync --locked --managed-python --python "$python_version" --extra dev --extra audio_macos
if [[ ! -x "$media/prefix/bin/ffmpeg" || ! -x "$media/prefix/bin/ffprobe" || ! -d "$media/wheels" ]]; then
  if [[ -e "$media" ]]; then media="$output/media"; fi
  bash native_build/macos/build_media.sh "$sources" "$media"
  rm -rf "$media/src"
fi
uv pip uninstall opencv-python opencv-python-headless
uv pip install --no-deps --reinstall "$media"/wheels/*.whl
uv run --no-sync python -c 'from cv2.dnn import blobFromImage'

export UV_NO_SYNC=1
export PYTHONPATH="$PWD"
uv run python -m scripts.build_release --target macos-arm64 --macos-release \
  --ffmpeg-helper "$media/prefix/bin/ffmpeg" --ffprobe-helper "$media/prefix/bin/ffprobe" \
  --out-dir "$output/runtime" --work-dir "$output/work"
runtime="$output/runtime/Editarr"
jq -e --arg revision "$revision" --arg version "$version" \
  '.source_revision == $revision and .source_dirty == false and .version == $version and .target == "macos-arm64"' \
  "$runtime/editarr-runtime-inventory.json" >/dev/null
uv run python -m scripts.package_macos --runtime "$runtime" --notices "$notices" \
  --out-dir "$output/package" --work-dir "$output/work"
jq -e '.architecture == "arm64" and .minimum_macos == "26.2"' \
  "$output/package/editarr-macos-package.json" >/dev/null

asset_base="Editarr-$tag-macOS"
dmg="$output/assets/$asset_base-arm64.dmg"
cp "$output/package/Editarr.dmg" "$dmg"
hdiutil verify "$dmg"
verify_dir=$(mktemp -d "$output/work/verify.XXXXXX")
mkdir "$verify_dir/mount"
hdiutil attach -readonly -nobrowse -mountpoint "$verify_dir/mount" "$dmg"
trap 'hdiutil detach "$verify_dir/mount" >/dev/null || true' EXIT
ditto "$verify_dir/mount/Editarr.app" "$verify_dir/Editarr.app"
hdiutil detach "$verify_dir/mount"
trap - EXIT
cmp "$notices/THIRD-PARTY-NOTICES.md" \
  "$verify_dir/Editarr.app/Contents/Resources/Licenses/THIRD-PARTY-NOTICES.md"
codesign --force --deep --sign - "$verify_dir/Editarr.app"
codesign --verify --deep --strict "$verify_dir/Editarr.app"
uv run python -m scripts.verify_installed_release --target macos-arm64 \
  --runtime "$verify_dir/Editarr.app" --recovery --json-out "$output/installed-verification.json"
jq -e --arg revision "$revision" --arg version "$version" \
  '.source_revision == $revision and .version == $version and .failures == [] and .checks.scratch_cleanup.removed == true' \
  "$output/installed-verification.json" >/dev/null

(
  cd "$output/assets"
  export COPYFILE_DISABLE=1
  tar -czf "$asset_base-Notices.tar.gz" -C "$notices" .
  tar -czf "$asset_base-Third-Party-Source.tar.gz" -C "$sources" .
  shasum -a 256 "$asset_base-arm64.dmg" "$asset_base-Notices.tar.gz" \
    "$asset_base-Third-Party-Source.tar.gz" > SHA256SUMS.txt
  shasum -a 256 -c SHA256SUMS.txt
)
