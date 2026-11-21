# macOS native release inputs and notices

The first native release targets Apple Silicon on macOS 26.2 and later. It
installs the AppKit launcher and embeds Editarr's Python runtime and web UI.
Follow the [local signing steps](../native_build/macos/README.md) after copying
the app from the disk image to Applications.

## Media libraries and models

- FFmpeg 8.1.3 and ffprobe build from the same upstream source, with x264 at
  `b35605ace3ddf7c1a5d67a2eb553f034aef41d55` and LAME 4.0. The build enables
  GPL components and ships the FFmpeg GPL-2.0-or-later terms with their source.
  Editarr uses x264 and LAME to encode browser previews.
- PyAV 18.1.0 uses those same shared FFmpeg libraries. OpenCV 5.0.0.93 uses its
  source-distribution image codecs and is built without FFmpeg. Editarr uses
  PyAV to decode media during scene detection in this release.
- Editarr downloads model weights on demand from pinned upstream repositories.
  The Storage section of Models shows the license metadata for each artifact. The DMG includes
  the NudeNet model supplied by its MIT-licensed Python package.

`Editarr.app/Contents/Resources/Licenses` contains the license texts and the
release notice. The GitHub release provides the corresponding source archives,
including the build recipe, the OpenCV packaging patch, and the media source
checksums. Editarr's own source is available at the release tag.

## Rebuild the media inputs

Use Xcode 26 or later and Python 3.11. Select Xcode with `DEVELOPER_DIR` for the
packaging command if the standalone command-line tools are selected globally. From a checkout of the release
tag, while the build environment has the locked Editarr dependencies, run:

```sh
uv sync --locked --extra dev --extra audio_macos
bash native_build/macos/build_media.sh /path/to/macos-sources /path/to/new-media-output
uv pip uninstall opencv-python opencv-python-headless
uv pip install --no-deps --reinstall /path/to/new-media-output/wheels/*.whl
UV_NO_SYNC=1 uv run python scripts/build_release.py --target macos-arm64 --macos-release \
  --ffmpeg-helper /path/to/new-media-output/prefix/bin/ffmpeg \
  --ffprobe-helper /path/to/new-media-output/prefix/bin/ffprobe \
  --out-dir /path/to/runtime-output
UV_NO_SYNC=1 uv run python scripts/package_macos.py --runtime /path/to/runtime-output/Editarr \
  --notices /path/to/notices --out-dir /path/to/package-output
```

The native runtime smoke exercises the embedded helpers, scene detection and
MP3/MP4 previews with generated media. It also loads a generated module with the
pinned d1-3B remote code's imports through the transformers remote-code loader
and runs a tiny LFM2 forward pass, without weights. With a transformers release
too old for d1-3B, it instead confirms that d1-3B is reported unavailable. Run the
installed app verifier on a locally signed copy from the DMG before distributing it.

On a Mac that already has d1-3B in its model cache, the frozen executable can
also run one real frame through the model. The probe rehashes the cached artifact,
loads only that copy with the Hugging Face Hub offline, and never downloads:

```sh
EDITARR_FROZEN_RUNTIME_SMOKE=1 EDITARR_FROZEN_RUNTIME_SMOKE_MODEL=d1-3b \
  EDITARR_FROZEN_RUNTIME_SMOKE_FRAME=/path/to/frame.jpg \
  Editarr.app/Contents/Resources/runtime/Editarr/Editarr
```
