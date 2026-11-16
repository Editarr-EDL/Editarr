# RP-10 — Package frontend with the production service

Dependencies: RP-05. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

pyproject.toml currently packages scanner only, and web/dist is ignored. A production
installation must include the built frontend without requiring Node/Vite at runtime.
The same artifact must work at different Base URLs. This is Python/package-based
production delivery; native executables and deployment Docker images are deferred.

## Read and expected touch points

Read pyproject.toml, uv.lock, web/package.json, web/pnpm-lock.yaml, web/vite.config.ts,
.gitignore, .dockerignore, scripts/, and RP-05's asset lookup/layout contract.

## Implementation steps

1. Add an explicit reproducible release build procedure using the existing package
   tooling and frontend lockfile. Build frontend assets first, then include them in
   the Python release artifact at the location expected by RP-05.
2. Define/document behavior for building from a source checkout/source distribution,
   including missing build prerequisites. Do not quietly ship a wheel without the UI.
3. Avoid per-deployment Base URL build variables. Include the RP-04 runtime template/
   bootstrap contract and content-hashed assets unchanged for root/prefixed installs.
4. Resolve installed assets through package resources/approved install layout,
   independent of cwd and repo presence. Check paths with spaces.
5. Include only required assets and runtime package files; exclude secrets, databases,
   development configuration, node_modules, and unrelated source artifacts.
6. Run the installed editarr from outside the checkout with temporary data and the
   approved Base URL settings. Confirm actual setup UI and API are served together.
7. Reuse a suitable existing dependency environment if heavy ML dependencies make a
   full clean install expensive, but explicitly separate wheel-content/asset evidence
   from any clean-install claim. Do not remove runtime dependencies to fake success.
8. Record the future Windows/macOS/Linux executable option without selecting tooling.

## Localhost requirement update

Launch the installed artifact and its normal setup/login with no Base URL or cookie
flags, in temporary working/data directories. Do not secretly supply generated URL
configuration to make this case pass. Retain explicit proxy URL smoke tests.

## Acceptance criteria

- [x] Release artifact includes required HTML/JS/CSS/icons and serves them after install.
- [x] Build fails clearly when required frontend output is unavailable.
- [x] Runtime does not invoke Node, pnpm, or a Vite server.
- [x] Identical artifact serves root and prefixed Base URLs without rebuilding.
- [x] Installed assets are found outside the repository and with a different cwd.
- [x] Private/runtime files and development dependencies are absent from packaged assets.
- [x] Repeat builds follow documented lockfile/tool inputs; content/layout is reproducible.
- [x] No production Docker or native-executable implementation is introduced.

## Verification

Run the release build, inspect archive contents, install into an isolated location,
and launch from a temporary working directory. Fetch actual HTML/assets and auth
status. Reuse this artifact for a second Base URL. Record exact commands, package
identity, dependency setup, and any network/platform limitations.

## Definition of done

Shared DoD applies. A concrete installable artifact serves UI/API without Vite and
its build/install commands are recorded. RP-12 and RP-13 can consume the artifact;
generated build output remains uncommitted.

## Handoff

Status: DONE

Last completed substep: Installed the release wheel without resolving dependencies,
then ran the bounded installed-artifact verifier from a temporary cwd with spaces. The
same installation passed permanent-localhost setup/session and explicit prefixed Base
URL UI/API/asset smokes; every created controller process group and temp data directory
was cleaned by the verifier.

Files changed: `pyproject.toml`; `.gitignore`; `web/package.json`;
`scripts/build_release.py`; `scripts/hatch_build.py`; `scripts/release_assets.py`;
`scripts/verify_installed_release.py`; `docs/release-build.md`; this task; task-pack
README checklist. Generated `web/dist`, `scanner/frontend`, artifacts, installation,
database, and smoke data remain ignored or outside the checkout.

Contract/API changes: `uv run python scripts/build_release.py` is the production
release entry point. It requires pnpm 11.22.0, installs exactly the frontend lock,
rebuilds Vite output, validates it, temporarily stages it at `scanner/frontend`, and
invokes Hatch with a stable default `SOURCE_DATE_EPOCH`. Hatchling 1.32.0 is pinned.
The Hatch hook allows editable source installs but rejects wheel/sdist builds without
complete compatible frontend assets and force-includes the ignored staging tree.
The sdist contains the built frontend so its wheel build needs no Node toolchain.
Production assets retain relative initial URLs and RP-05 replaces the inert bootstrap
at runtime; no deployment-specific frontend input was added. Installed runtime PATH
in the smoke omitted Node/pnpm, and no runtime code invokes them or Vite.

Verification commands and results:

- `UV_OFFLINE=1 UV_CACHE_DIR=/path/to/Editarr/.uv-cache uv run
  python scripts/build_release.py --out-dir '/tmp/Editarr RP10 build one'` and the
  same command with `/tmp/Editarr RP10 build two` passed with uv 0.12.5, Python
  3.11.16, Node 26.7.0, pnpm 11.22.0, Vite 6.4.3, and Hatchling 1.32.0 on macOS
  arm64. Both produced byte-identical artifacts. Wheel SHA-256:
  `3deef8e6504d731d0f46574c4a77bb818c344f6caae7c9fe8ef6da862f0294ff`;
  sdist SHA-256:
  `22c5338905af4a9909db191f2c1ccdcd6814eec38af92ee920c1eb8f39a080b8`.
- Archive inspection showed the wheel contains only runtime Python files, metadata,
  and nine required frontend files: entry HTML, hashed JS/CSS, and six icons. The
  minimal sdist adds only its build hook/validator, README, project metadata, and the
  same frontend. Neither contains `.env`, databases/data, Node modules, web sources,
  tests, or caches.
- `uv build --wheel '/tmp/Editarr RP10 build one/editarr-0.1.0.tar.gz'` passed offline;
  its wheel was byte-identical to the original. Direct `uv build --wheel` without
  staging failed with `Required production frontend is unavailable` and the exact
  release command. Removing the installed frontend produced RP-05's actionable
  `FrontendAssetsError`; assets were then restored.
- `uv venv ... '/tmp/Editarr RP10 installed env'` plus `uv pip install --no-deps ...whl`
  installed `editarr==0.1.0`; `scanner` resolved from that environment and all nine
  frontend files resolved beneath its site-packages directory.
- `.venv/bin/python -u scripts/verify_installed_release.py --editarr
  '/tmp/Editarr RP10 installed env/bin/editarr' --dependency-site-packages
  '/path/to/Editarr/.venv/lib/python3.11/site-packages'` passed
  under escalated localhost binding.
  From a separate temp cwd with spaces, localhost HTML and hashed asset returned 200,
  auth status changed from setup-required to authenticated, and the forms cookie was
  not Secure without any Base URL/cookie variables. Reusing the same installation
  with `https://proxy.example.test/media/editarr` and normal Secure-cookie policy
  returned 200 for prefixed HTML, asset, and auth API, emitted matching base/API
  bootstrap paths, and returned 404 for the root API copy. HTTP was only the direct
  upstream test transport; the public URL remained HTTPS.
- Focused Ruff passed for all release scripts and RP-05 serving files. `uv run pytest
  -q tests/test_production_frontend.py tests/test_backend_routing.py tests/test_cli.py`
  passed 35 tests with five existing FastAPI/Starlette deprecation warnings.
  `git diff --check` passed.

Acceptance criteria still unmet: None.

Approvals received or outstanding: RP-00 already approved package-based delivery,
one runtime-configurable build, permanent localhost, and Secure cookies for an HTTPS
public URL. No database migration, wipe, new product decision, or approval was needed.

Known failures/limitations: Dependency reuse was deliberate: the wheel itself was
isolated and installed with `--no-deps`, while runtime dependencies came from the
existing locked project environment. This is not evidence of a clean network install
of the large ML dependency graph. pnpm's optional update metadata request could not
resolve in the restricted environment, but the frozen offline dependency installation
and build completed. The prefix smoke configured an HTTPS public URL but connected to
the upstream directly over HTTP while retaining normal Secure-cookie policy;
RP-12/RP-13 own actual TLS proxy verification. Archive identity is demonstrated for
the recorded toolchain and platform; different Node, Python, or platform versions
remain documented metadata inputs. Native executables and production Docker remain
deferred.

Next exact action: RP-12 can consume either recorded artifact and use
`scripts/verify_installed_release.py` as the direct-listener baseline before testing
Nginx/Caddy prefix-preserving fixtures. RP-13 should exercise the same artifact through
the production proxy fixtures and restart lifecycle.
