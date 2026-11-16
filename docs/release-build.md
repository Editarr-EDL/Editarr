# Building an Editarr Python release

Editarr's production wheel includes the Vite output under `scanner/frontend`. A
production installation therefore serves the browser application and API from the
`editarr` process without Node.js, pnpm, Vite, or a source checkout at runtime.

From a source checkout, install Python 3.11 or newer, `uv`, Node.js, and pnpm 11.22.0,
then run the build below. The installed app needs a Python whose `sqlite3` module
reports SQLite 3.35 or newer, because its migrations use `DROP COLUMN` and `RETURNING`:

```console
uv run python scripts/build_release.py --wheel
```

The `--wheel` flag is required for this developer-oriented Python artifact. The native
packaging work tracked by the native application plan owns the script's default frozen build
mode and its target-specific helper inputs; it is not an end-user release claim here.

Native builds require Go 1.25 or newer in addition to the existing frontend,
Python and target-native ffprobe prerequisites. The build compiles the pinned
`filippo.io/age` v1.3.2 sidecar on the target host, bundles it under
`_internal/helpers/backup-age` (or `backup-age.exe` on Windows), and verifies
an encrypted round trip using the collected executable with a restricted PATH.
The installed native runtime does not need Go, age or Expect. Developer wheels
retain the age/Expect bridge on POSIX for optional encryption; plain backups do
not require either executable.

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
Vite Base URL or rebuild for each deployment. Native Windows, macOS, and Linux
applications remain future delivery work governed by the
[native application distribution plan](native-application-plan.md); this procedure does
not implement that plan or add an executable bundler. Production Docker images remain
outside this release scope.

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

## RP-13 release acceptance evidence

The final current-worktree acceptance build used
`UV_OFFLINE=1 uv run python scripts/build_release.py --out-dir '/tmp/Editarr RP13 build'`.
Its wheel SHA-256 is `4ae7a2237d245303a2a332dedab156839552d3973a982b00388242c2cfff0c05`
and its sdist SHA-256 is `3b2b53fda9bc2aaeedfa76a3253230f07373e405a522846a5e69d86790b43ee8`.
The wheel was installed with `--no-deps` into a fresh temporary environment that reused
the locked project dependency site-packages only for verification; that is not a clean
network-install claim. A real Nginx TLS Chromium run against that fresh wheel passed
setup/login, protected artwork and preview, a staged prefix/port save and restart, a
second-origin reconnect/sign-in, cookies, CSRF, assets, and logout. Earlier same-artifact
matrix runs also passed Nginx root/nested and Caddy root. The exact disposable-fixture
commands, broad test counts, and external-media/dependency limitations are recorded in
[RP-13's handoff](reverse-proxy-production-tasks/13-production-verification.md).

## MTS-07 timing and credits evidence

The September 20, 2026 current-worktree verification covered affine MCF import, repeated
review retiming, credits scan lifecycle and acceptance, canonical MCF/EDL output, frontend
workflows, accessibility, runtime ownership, and packaging:

- Ruff passed across `scanner`, `tests`, `scripts`, and `native_build`; 1,189 backend tests
  passed without warnings. The MTS final-integration suite includes a generated-media journey
  proving immutable source timing, mapped review timing, edited credits acceptance, canonical
  MCF bounds and payload, and the physical-END EDL Skip.
- All 123 Vitest tests and the production frontend build passed. All 39 mocked Chromium tests
  passed, including desktop and 320px timing/credits journeys with axe, root-overflow, focus,
  decoded clip playback, sampled nonblank pixels, and screenshots under `web/test-results/`.
- The deterministic detector evaluation passed 7/7 policy cases and 7/7 rendered-tail cases
  within tolerance with zero false positives; the retained PySceneDetect comparison passed
  6/7 with zero false positives. No Qwen configuration or real-film corpus was exercised, so
  this is regression evidence rather than a population-accuracy claim.
- The offline wheel/sdist build passed after removing duplicate wheel inclusion of the staged
  frontend. The wheel SHA-256 was
  `2a1111abae03ee97b788f7c057ab0df6842a7d8dd022ed9f350302bed20733b3`; the sdist SHA-256 was
  `9f326d5058ac51d83b75fad1272073503ae2a44049d36e8e876fd715f48f3e01`. An isolated wheel
  install returned HTTP 200 with no verifier failures, but remains classified by the verifier
  as `legacy-unverified`, not native release readiness.
- The target-native macOS frozen build stopped safely before artifact creation because this
  host lacks the required tracked relocatable `ffprobe` helper. Native/runtime Python fixture
  alternatives passed 68 tests. Swift shared tests could not compile because XCTest is absent
  from the active Command Line Tools environment. These are explicit environment-only limits;
  no target artifact, signing, notarization, or cross-platform claim is inferred.

All automation used temporary databases, generated media, mocked browser APIs, and isolated
`/tmp` runtime/install roots. It did not acquire models or access an installed/user database,
real media, credentials, sidecars, or running Editarr service.

## CSS-67 community packaging

The [community user guide](community-user-guide.md) documents connection, manual sharing,
Find Filters, review reuse, initial setup restore and portable key recovery. Community
service packaging is independent of this Python release; use its
[Docker runbook](community-service-docker.md) for operator moderation, image replacement,
offline snapshots, upgrades and disabled new-volume restore.

October 7, 2026 verification built wheel and sdist twice with identical bytes from
source revision `b5964fe34dfc215dcaf1ab4bc8346f1a3eb6bb25` plus uncommitted CSS-67
changes. Python 3.11.16, pnpm 11.22.0 and the pinned build backend were used;
`SOURCE_DATE_EPOCH` retained the builder's default `315532800`.

- Wheel SHA-256: `4b7fc9570ca3e31e48152b7c05572526c2039faec900dba50cda7ce76518a465`.
- Sdist SHA-256: `74763e98c77200badf6fd889d5c6b67d690028ab6753b066208d66568ca45e38`.
- Both archives contain packaged local migrations through `0007_community_publications`,
  community codecs/signatures/key workflows, outboxes, backup support, local UI and
  embedded build metadata, without community operator assets or service secrets.
- The isolated wheel/sdist test rebuilds the sdist, starts its packaged migrations,
  and runs existing setup-restore and verified-import tests from copied public fixtures.
  Node is absent from that runtime PATH; application imports resolve inside the wheel.
  Both plain/encrypted archives and both administrator outcomes execute on this POSIX host.
- An actual fresh wheel install returned setup readiness 200 with zero verifier failures.
  Dependencies were reused explicitly via `--dependency-site-packages`; this is not a
  clean dependency install or native certification. The verifier labels it `legacy-wheel`.
- The local frontend's existing 545.43 kB chunk advisory remains. No UI behavior changed.

Native macOS freezing was not certified: a tracked relocatable target ffprobe input and
full native toolchain evidence are still required. Windows/Linux target artifacts,
AMD64 community deployment and Mac/FileVault/Docker/tunnel cold boot are unverified,
not passed or silently removed from launch scope. The user selected fresh deployment
without legacy import and explicitly retained unavailable checks as open release gates.
The [artifact and platform matrix](community-scan-service-tasks/decision-evidence/67-packaging-matrix.md)
contains reproduction commands and inventory. October 7: the user canceled CSS-68
for local Docker on Mac mini; [CSS-69](community-scan-service-tasks/69-pilot-and-launch-scope.md)
records the completed disabled local-only pilot rehearsal and remaining future
public release gates, without authorizing publication or activation.
