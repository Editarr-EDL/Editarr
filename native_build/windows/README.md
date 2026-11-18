# Windows package inputs

`EditarrLauncher.cpp` is a small Win32 tray shell. It owns no HTTP or scan behavior.
It starts the embedded runtime without a console and uses the authenticated
`lifecycle` commands for status, Open Editarr, Open Logs, Stop, and Quit.
Launch at Login is a per-user `HKCU` Run value; no service, elevation, firewall rule, or
machine-wide state is used.

`Editarr.iss` is the per-user Inno Setup definition. The packaging wrapper supplies the
canonical project version, stable product ID, approved Publisher identity, and isolated
staging/output paths as preprocessor values. Normal uninstall removes the application and
launch-at-login value but deliberately has no instruction that removes `%LOCALAPPDATA%\Editarr`.

The release workflow builds an unsigned alpha installer using `--unsigned-alpha`.
It requires `--notices` and installs those notices alongside the app. The workflow uses
`build_media.sh` under MSYS2 UCRT64 and `build_media_wheels.ps1` under MSVC to
build FFmpeg, ffprobe, PyAV, and OpenCV from the sources accompanying each release.

For the standalone evidence workflow, pass `--unsigned-development`. That mode uses the literal metadata
`Unsigned development build - no publisher identity`, names the artifact
`*-unsigned-development.exe`, and marks its evidence `development_only=true` and
`signed=false`. Production mode requires all of `--publisher`, `--certificate`, and an
HTTPS `--timestamp-url`; it cannot silently fall back to unsigned output.

Run `uv run python scripts/package_windows.py --help` on Windows x64 for the build contract.
Generated launchers, installers, staging trees, signature material, and evidence stay
outside the checkout and are not committed.
