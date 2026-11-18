#!/usr/bin/env bash
# Exercise the finished AppImage on a current Ubuntu runner.
set -euo pipefail
task_root=${1:?provide the build output directory}
sudo apt-get update
sudo apt-get install --yes appstream desktop-file-utils fuse3 libfuse2t64 xvfb
package_root="$task_root/package output"
appimage="$package_root/$(jq -er .appimage "$package_root/editarr-linux-package.json")"
test "$(uname -m)" = x86_64
verification_flags=()
if [[ -z ${VERSION:-} ]]; then
  verification_flags+=(--test-fake-models)
fi
cd "$task_root/tmp"

(cd "$GITHUB_WORKSPACE" && python3 -m scripts.verify_installed_release \
  --runtime "$package_root/Editarr.AppDir/usr/lib/editarr" \
  --target linux-x86_64 \
  "${verification_flags[@]}" \
  --json-out "$task_root/native14-ubuntu-appdir-verifier.json")

newer_xdg="$task_root/ubuntu-xdg"
mkdir -p "$newer_xdg"/{home,data,config,state,cache,runtime,tmp}
chmod 700 "$newer_xdg/runtime"
common_environment=(
  "HOME=$newer_xdg/home"
  "TMPDIR=$newer_xdg/tmp"
  "XDG_DATA_HOME=$newer_xdg/data"
  "XDG_CONFIG_HOME=$newer_xdg/config"
  "XDG_STATE_HOME=$newer_xdg/state"
  "XDG_CACHE_HOME=$newer_xdg/cache"
  "XDG_RUNTIME_DIR=$newer_xdg/runtime"
)
extract_root="$task_root/extracted fallback"
mkdir "$extract_root"
cd "$extract_root"
"$appimage" --appimage-extract >/dev/null
env "${common_environment[@]}" ./squashfs-root/AppRun paths \
  > "$task_root/native14-ubuntu-extracted-paths.txt"
grep -F "$newer_xdg/cache/editarr" \
  "$task_root/native14-ubuntu-extracted-paths.txt"
if [[ -n ${VERSION:-} ]]; then
  test ! -e squashfs-root/usr/lib/editarr/_internal/native-test-fake-models.marker
fi

launch="$PWD/squashfs-root/AppRun"
model_check_mode=extract
if test -c /dev/fuse && env "${common_environment[@]}" "$appimage" paths > "$task_root/native14-ubuntu-fuse-paths.txt"; then
  grep -F "$newer_xdg/data/editarr" "$task_root/native14-ubuntu-fuse-paths.txt"
  launch="$appimage"
  model_check_mode=fuse
else
  echo "FUSE is unavailable on this runner. Exercising the extracted AppImage."
fi

headless_xdg="$task_root/headless-xdg"
mkdir -p "$headless_xdg"/{home,data,config,state,cache,runtime,tmp}
chmod 700 "$headless_xdg/runtime"
headless_environment=(
  "HOME=$headless_xdg/home"
  "TMPDIR=$headless_xdg/tmp"
  "XDG_DATA_HOME=$headless_xdg/data"
  "XDG_CONFIG_HOME=$headless_xdg/config"
  "XDG_STATE_HOME=$headless_xdg/state"
  "XDG_CACHE_HOME=$headless_xdg/cache"
  "XDG_RUNTIME_DIR=$headless_xdg/runtime"
  "EDITARR_AUTH_COOKIE_SECURE=false"
)
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
env -u DISPLAY -u WAYLAND_DISPLAY -u BROWSER \
  "${headless_environment[@]}" "$launch" --port "$port" &
headless_pid=$!
cleanup() {
  env "${headless_environment[@]}" "$launch" lifecycle quit >/dev/null 2>&1 || true
  kill "$headless_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT
for attempt in $(seq 1 90); do
  if grep -Fq "Editarr is ready at http://127.0.0.1:$port/" \
    "$headless_xdg/state/editarr/controller.log" 2>/dev/null; then
    break
  fi
  if ! kill -0 "$headless_pid" 2>/dev/null; then
    exit 1
  fi
  sleep 1
done
grep -F "Editarr is ready at http://127.0.0.1:$port/" \
  "$headless_xdg/state/editarr/controller.log"
env "${headless_environment[@]}" "$launch" lifecycle quit
wait "$headless_pid"
trap - EXIT

graphical_xdg="$task_root/graphical-xdg"
mkdir -p "$graphical_xdg"/{home,data,config,state,cache,runtime,tmp}
chmod 700 "$graphical_xdg/runtime"
browser_log="$task_root/native14-browser-url.txt"
browser_stub="$task_root/browser-stub"
printf '#!/bin/sh\nprintf "%%s\\n" "$1" > "%s"\n' "$browser_log" > "$browser_stub"
chmod 755 "$browser_stub"
graphical_environment=(
  "HOME=$graphical_xdg/home"
  "TMPDIR=$graphical_xdg/tmp"
  "XDG_DATA_HOME=$graphical_xdg/data"
  "XDG_CONFIG_HOME=$graphical_xdg/config"
  "XDG_STATE_HOME=$graphical_xdg/state"
  "XDG_CACHE_HOME=$graphical_xdg/cache"
  "XDG_RUNTIME_DIR=$graphical_xdg/runtime"
  "EDITARR_AUTH_COOKIE_SECURE=false"
  "DISPLAY=:99"
  "BROWSER=$browser_stub"
)
Xvfb :99 -screen 0 1280x720x24 >"$task_root/xvfb.log" 2>&1 &
xvfb_pid=$!
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
env "${graphical_environment[@]}" "$launch" --port "$port" &
graphical_pid=$!
cleanup() {
  env "${graphical_environment[@]}" "$launch" lifecycle quit >/dev/null 2>&1 || true
  kill "$graphical_pid" "$xvfb_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT
for attempt in $(seq 1 90); do
  test -s "$browser_log" && break
  if ! kill -0 "$graphical_pid" 2>/dev/null; then
    exit 1
  fi
  sleep 1
done
grep -Fx "http://127.0.0.1:$port/" "$browser_log"
env "${graphical_environment[@]}" "$launch" --port "$port"
env "${graphical_environment[@]}" "$launch" lifecycle quit
wait "$graphical_pid"
kill "$xvfb_pid" >/dev/null 2>&1 || true
wait "$xvfb_pid" 2>/dev/null || true
trap - EXIT

desktop-file-validate \
  "$package_root/Editarr.AppDir/usr/share/applications/org.editarr.Editarr.desktop"
appstreamcli validate --no-net \
  "$package_root/Editarr.AppDir/usr/share/metainfo/org.editarr.Editarr.metainfo.xml"
(cd "$package_root" && sha256sum --check "$appimage.sha256")
if [[ -z ${VERSION:-} ]]; then
  (cd "$GITHUB_WORKSPACE" && python3 -m scripts.verify_linux_models \
    --runtime "$package_root/Editarr.AppDir/usr/lib/editarr" \
    --appimage "$appimage" --mode "$model_check_mode" \
    --json-out "$task_root/native14-ubuntu-real-models.json")
fi
git -C "$GITHUB_WORKSPACE" diff --check
