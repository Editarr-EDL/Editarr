# Building Editarr releases

## Automatic macOS releases

Every new first-parent commit on `main` gets an Apple Silicon release. A push
with several new main commits builds each revision. A PR merge counts as one
main commit, and its release notes list the commits that the merge introduced.
Versions start at `v0.1.4` and increase by one patch per commit after the
`v0.1.3` anchor. The Git history makes the version stable across retries.

The workflow builds a clean, generated release commit whose parent is the
verified main revision. Its only changes set the Python, web, and lockfile
versions. The release tag points to that generated commit.

The macOS runner builds the DMG and verifies a disposable installed copy,
including recovery. It produces the DMG, license notices, matching third-party
source archive, and SHA-256 checksums. The publishing job uploads those four
files to a draft, checks GitHub's reported file sizes and digests, and publishes
the release for download. Re-run a failed workflow to retry its commit using
the same version. A verified older release can publish without replacing the
latest download.

Use **Run workflow** on the Editarr release workflow for a build-and-verify run
of a selected ref. The files and verifier report appear in the
workflow run's artifacts. The workflow uses the source and notice archives
from Editarr v0.1.2 as checksum-pinned macOS build inputs. Update those inputs
and the media recipe when changing the corresponding pinned toolchain or
source dependencies.

## Developer wheel

Editarr's production wheel includes the Vite output under `scanner/frontend`. A
production installation therefore serves the browser application and API from the
`editarr` process without Node.js, pnpm, Vite, or a source checkout at runtime.

From a source checkout, install Python 3.11 or newer, `uv`, Node.js, and pnpm 11.22.0,
then run the build below. The installed app needs a Python whose `sqlite3` module
reports SQLite 3.35 or newer, because its migrations use `DROP COLUMN` and `RETURNING`:

```console
uv run python scripts/build_release.py --wheel
```

Use `--wheel` to build the developer Python artifact. For native app inputs and
packaging, see [macOS](macos-release.md), [Windows](../native_build/windows/README.md),
and [Linux](../native_build/linux/README.md).

Developer wheels use the age/Expect bridge on POSIX for optional backup
encryption. Plain backups require neither executable. Native builds use the
embedded `backup-age` helper built from `filippo.io/age` v1.3.2 with Go 1.25
or newer on the target host.

The build script runs `pnpm install --frozen-lockfile`, removes any earlier `web/dist`,
runs the TypeScript/Vite production build, validates it, stages only that output at
the installed package path, and invokes `uv build`. It always removes the temporary
`scanner/frontend` staging directory. Both generated directories and `dist/` are
ignored by Git. `SOURCE_DATE_EPOCH` defaults to `315532800`; set it explicitly if a
release process requires a different stable timestamp.

Direct `uv build` from a checkout without staged frontend assets fails with an
instruction to use the release script. The source distribution already contains the
validated built frontend, so building its wheel needs Python and the build backend,
but no frontend toolchain. A damaged source distribution missing those assets fails
in the same validation hook. The sdist intentionally contains the Python runtime,
packaged frontend, build hook, project metadata, and README; it omits the web source,
tests, local data, environment files, Node dependencies, and development output.

The frontend dependency graph is frozen by `web/pnpm-lock.yaml`, and the project
environment is frozen by `uv.lock`; wheel metadata retains the runtime constraints in
`pyproject.toml`. The build backend is pinned there, and pnpm is pinned in
`web/package.json`. The script prints the SHA-256 digest of every bundled frontend
file. Repeating a build with the same source, locked dependencies, toolchain, platform, and
`SOURCE_DATE_EPOCH` should produce identical archives. Different Node.js, Python, or
platform versions are documented build inputs and can change generated metadata even
when the served frontend bytes are identical.

Closing-credits detection adds no bundled model or release-only executable. Its authored
CC0 evaluation manifest and generated renderer inputs remain test/development data, not
runtime package data. Production detection uses packaged Python modules plus the existing
OpenCV/PySceneDetect runtime dependencies. Optional pinned Qwen artifacts remain managed
downloads listed in `scanner/model-manifest-v1.json`; a missing artifact is never acquired
without informed user consent.

Runtime credits results are regenerable JSON under the resolved cache root at
`credits/<sha256-prefix>/<sha256>.json`. The digest includes the video hash and every
behavior-affecting detector, evidence, threshold, scene-cache, frame-cache, prompt, and Qwen
model identity. Per-run frames and preview intermediates use ownership-scoped temporary scan
workspaces and are removed after execution. Neither location is package data, and frozen or
installed verification must redirect both through isolated runtime paths rather than writing
inside the installation or source checkout.

To exercise an artifact outside the checkout, create an environment and install the
wheel normally, then launch the installed command. The command serves the bundled UI and
API together; Node, pnpm, Vite, and the source checkout are not runtime dependencies.

```console
uv venv /opt/editarr/venv --python 3.11
uv pip install --python /opt/editarr/venv/bin/python /absolute/path/editarr-0.1.0-py3-none-any.whl
/opt/editarr/venv/bin/editarr --host 127.0.0.1
```

With no Base URL configuration this serves the normal, long-lived
`http://localhost:3348/` deployment. Use `--port` only as a temporary listener override;
save a lasting listener Port in General Settings. `--api-only` is deliberately an
API-only/development mode and does not require packaged frontend files. A normal launch
instead reports a missing/incompatible frontend artifact clearly. For public HTTPS,
provide `EDITARR_BASE_URL` before first launch or save it after setup, and place a
prefix-preserving TLS proxy in front of the private upstream as shown in
[the proxy examples](examples/reverse-proxy/README.md).

The same wheel supports root and path-prefixed Base URLs at runtime. Do not set a
Vite Base URL or rebuild for each deployment.

Release verification can launch an already installed wheel from a separate working
directory with isolated temporary data:

```console
python scripts/verify_installed_release.py \
  --editarr /path/to/installed/bin/editarr \
  --dependency-site-packages /path/to/dependency/site-packages
```

The dependency path is intended for release testing when downloading the complete ML
stack again would be wasteful. It is not a clean-install claim. The verifier removes
inherited `EDITARR_*` values, proves the zero-configuration localhost setup/session
path, then restarts the same installed artifact with an HTTPS Base URL prefix. Its
second smoke retains the normal Secure-cookie policy for that HTTPS public URL; HTTP
is used only as the direct connection to the upstream service without a TLS proxy.
