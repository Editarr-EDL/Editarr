# macOS package inputs

`EditarrLauncher.swift` is the small AppKit lifecycle shell used as the executable of
`Editarr.app`. It owns no HTTP or scan behavior. Start invokes the embedded
runtime; Open Editarr, Open Logs, status, Stop, and Quit use the authenticated
lifecycle adapter exposed by the runtime CLI. `SMAppService.mainApp` owns Launch at
Login.

The application starts as an `LSUIElement` menu-bar agent. General Settings can
show its Dock icon without restarting the service. The app has no App Sandbox
entitlement. Current macOS distribution policy deliberately ships an unsigned app and
DMG: it does not claim a Developer ID identity, hardened-runtime coverage, notarization,
stapling, or Gatekeeper acceptance. The distributable does not use `runtime.entitlements`.

After moving `Editarr.app` to `/Applications`, the end user performs this local ad-hoc
signing sequence in Terminal before opening it:

```sh
xattr -dr com.apple.quarantine /Applications/Editarr.app
codesign --force --deep --sign - /Applications/Editarr.app
codesign --verify --deep --strict --verbose=2 /Applications/Editarr.app
```

The final command must succeed. These commands establish only a local ad-hoc signature;
they do not assert publisher identity or replace the normal caution required for any
downloaded software.

The packager uses Xcode 26 or later to compile the Icon Composer asset. If the
command-line tools are selected globally, set
`DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` for the packaging command.
Run `uv run python scripts/package_macos.py --help` for the build contract. Generated
applications, disk images, icon sets, and evidence stay outside the checkout and are not
committed.
