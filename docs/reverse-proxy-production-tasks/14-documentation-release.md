# RP-14 — Finish operator docs and release acceptance

Dependencies: RP-13. Read [shared background and DoD](README.md), the parent plan,
and completed prerequisite handoffs before coding. RP-00's contract controls interfaces.

## Background and goal

Complete the operator-facing instructions and reconcile all documents with the
implemented deployment behavior. Do not leave the original path-only/env-authoritative
proposal mixed with the approved full Base URL and UI-precedence contract.

## Read and expected touch points

Read all task handoffs, RP-00 contract, README.md, docs/api-contract.md,
docs/feature-design-spec.md, parent plan, CLI help, packaging scripts, and proxy examples.
Use RP-13's recorded evidence rather than claiming support from intent.

## Implementation steps

1. Document reproducible release build/install/run with bundled UI, configurable
   upstream Port, and no Node/Vite at runtime. Distinguish API-only/development mode.
2. Explain full Base URL parsing, root default, nested prefixes, saved-setting-over-env
   precedence, explicit root overrides, CLI port override, and removal of
   EDITARR_AUTH_PUBLIC_ORIGIN without compatibility support. Clearly distinguish
   public and listening ports.
3. Explain config file location/schema, backups, atomic updates, offline recovery,
   first-run configuration requirements, and corrupt/missing config behavior.
4. Document General Settings Save versus Restart Editarr, pending values, dirty edits,
   CLI masking, actual service restart, scan continuity, and reconnect/sign-in behavior.
   Include Port/prefix proxy updates and bind-failure recovery.
5. Publish validated Nginx/Caddy configurations and trusted-header/network guidance.
   State the prefix-preserving contract and existing forms/external auth distinction.
6. Update API schemas/examples, public/protected route inventory documentation, cookie
   paths, runtime bootstrap description, and restart status/capability/error contracts.
7. Record future native Windows/macOS/Linux executable builds as a short deferred
   requirement only. Do not select a bundler, installer, signing strategy, or architecture.
8. Reconcile the parent plan and task checklist with actual completion. Record any
   outstanding approval or verification gap rather than marking everything done.
9. Review the final diff for secrets/generated output/unrelated changes and run
   git diff --check. Reuse recent RP-13 verification unless subsequent changes create
   a concrete need to rerun tests. Do not deploy, publish, push, or restart a real service.

## Localhost requirement update

Document running editarr at http://localhost:8000 with no Base URL configuration,
normal authentication, and indefinite local operation. Explain saved/CLI Port
changes and keep local HTTP cookie handling distinct from public HTTPS deployment.

## Acceptance criteria

- [x] A new operator can install/serve UI/API and configure either root or a prefix.
- [x] Documentation matches implemented names, defaults, precedence, lifecycle, and errors.
- [x] Configuration/restart/proxy recovery procedures are actionable and tested.
- [x] Public frontend assets are distinguished from protected data/docs without ambiguity.
- [x] Native executables and Docker remain accurately marked as deferred.
- [x] Every task marked complete has acceptance/verification evidence and a handoff.
- [x] Final changes contain no secrets, live data, generated dependencies, or unrelated edits.
- [x] Final report distinguishes automated checks, manual checks, and remaining limitations.

## Verification

Check relative Markdown links and referenced files/commands. Compare docs examples to
RP-00/RP-06/RP-08 implemented contracts and RP-12 actual proxy configs. Audit the RP-13
matrix, build artifact evidence, and recent regression results. Run additional tests
only for code changes made during this final audit.

## Definition of done

Shared DoD applies. Documentation and release evidence fully describe the delivered
behavior; all coding-task criteria are satisfied or transparently left incomplete.
Provide a concise final implementation summary with actual validation results and any
material limitations. No real deployment or publication is implied by task completion.

## Handoff

Status: DONE
Last completed substep: Reconciled the operator guide, release guide, API contract,
  product baseline, parent plan, checklist, proxy references, and release evidence with
  the verified RP-13 artifact and production browser matrix.
Files changed: README.md; docs/api-contract.md; docs/feature-design-spec.md;
  docs/release-build.md; docs/reverse-proxy-production-plan.md;
  docs/reverse-proxy-production-tasks/README.md; this task.
Contract/API changes: Documentation-only. The published contract now records the shipped
  optional full Base URL resolver, local default, settings/restart lifecycle,
  prefix-preserving proxy requirement, public-static/protected-route boundary, runtime
  bootstrap, cookie transition, and restart capability/error behavior. No runtime code,
  deployment, user service, data, database, or proxy configuration changed.
Verification commands and results:

- Read the full RP-00 contract and RP-01 through RP-13 handoffs; RP-13 is the
  authoritative release-evidence source.
- `UV_CACHE_DIR=/path/to/Editarr/.uv-cache uv run editarr --help`
  passed and confirmed `--host`, temporary `--port`, and `--api-only` wording.
- `UV_CACHE_DIR=/path/to/Editarr/.uv-cache uv run python
  scripts/run_development.py --help` and the `api`/`dev` help forms passed.
- Markdown relative-link/file audit and `git diff --check` passed after the documentation
  changes; `git diff --check --no-index /dev/null docs/release-build.md` emitted no
  whitespace errors for that pre-existing untracked release-guide file (its exit status
  is the expected changed-file `1`). No code changed, so no tests were rerun; reused
  RP-13's recorded current
  broad checks: Ruff passed; pytest 441 passed with five existing warnings; frontend
  unit 121 passed in the final parent review; mocked Playwright 30 passed; build
  passed; fresh Nginx Chromium
  production run passed one test in 5.5 seconds. Its wheel SHA-256 is
  `4ae7a2237d245303a2a332dedab156839552d3973a982b00388242c2cfff0c05` and sdist
  SHA-256 is `3b2b53fda9bc2aaeedfa76a3253230f07373e405a522846a5e69d86790b43ee8`.
Acceptance criteria still unmet: None.
Approvals received or outstanding: None. All documented behavior was already approved
  by RP-00 and verified by RP-13; no new product or architecture decision was made.
Known failures/limitations: The release browser/proxy evidence uses disposable test
  data, fixtures, certificates, ports, processes, and fake external media/renderer
  boundaries. The no-deps install reused locked project site-packages and is not a clean
  network dependency-install claim. Operators still own public DNS, certificates,
  firewall/service-manager setup, proxy reloads, and proxy-route changes. Docker/Compose
  and native Windows/macOS/Linux executable delivery are deferred. No real deployment
  was performed or published.
Next exact action: None for RP-14. Future release work should rebuild and independently
  verify a new artifact; do not reuse the RP-13 hashes for changed source.
