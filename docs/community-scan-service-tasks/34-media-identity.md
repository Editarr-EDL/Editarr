# CSS-34 — Normalize typed media and release metadata

Status: **DONE** (October 2, 2026). Typed discovery, sanitized real captures and
durable explicit Other Files confirmation are implemented and verified locally.

- Prerequisites: [CSS-08](08-provenance-schema.md)
- Decision gates: None for pure metadata normalization; CSS-35/36 resolve fingerprint/tolerance choices.
- Parent sections: contracts §3; main §6.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Radarr/Sonarr/Jellyfin integrations exist. Provider IDs must distinguish work/series/episode; local database IDs and rounded catalog runtimes are not global identity or measured timing.

## Starting points

scanner/integrations/; scanner/modeling/; scanner/community/ matching inputs; docs/adr/.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Add one shared typed identity normalizer using sanitized real upstream fixtures; preserve aliases with provenance.
2. Represent episode spans/numbering schemes, editions, measured runtime and audio language/role separately from display release names.
3. Support user-confirmed typed IDs for Other Files; supersede stale Jellyfin-only ADR statements without rewriting unrelated history.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Series/episode namespace conflicts, specials, double episodes and alternative numbering stay distinct.
- [x] Scene/group match cannot bypass known duration/audio conflicts.
- [x] Missing identifiers produce actionable input requirements; no public absolute paths or fabricated aliases.

## Verification

- Fixture tests for each existing integration and manual/filename fallback.
- Run catalog and integration regressions; verify the API contract still supports existing local discovery.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide normalized media DTOs, alias rules and source fixtures to CSS-35/36/37/48.

## Handoff summary for the next task

Status / date / implementer: DONE, October 2, 2026, GitHub Copilot. Uncommitted
workspace work; prior CSS-33 and user changes are preserved.

Implemented outcome: Arr/Jellyfin discovery adopts the shared public identity
normalizer, including exact linked-series joins, Sonarr shared-file spans and
per-version Jellyfin release evidence. Local discovery remains compatible.
Other Files and selected scanned files reuse a typed editor with explicit
Review/Confirm, passive reload, namespace reset, alternative numbering, separate
shared-file choice, cancel/focus handling and durable file-bound backend state.
Scene/group cannot bypass known supplied file-duration/audio conflicts.

October 2 follow-up decision: TMDB/TVDB are the only supported identity providers.
Normalization ignores upstream IMDb fields, the manual API/editor reject IMDb,
and legacy stored confirmations are read without rewriting data. Mixed records
retain supported associations; IMDb-only records require reconfirmation.
TMDB/TVDB URL input remains in the CSS-37/46 sharing follow-up.
Follow-up verification: the six-file identity/auth/discovery pytest slice passed
100 tests; the identity/catalog rerun after formatting passed 48. Identity editor
Vitest passed 3 tests, Chromium passed both 320/1440px journeys, and type checking,
production build, changed-file Ruff and focused editor diagnostics passed.

Files/contracts/fixtures: See the [DTO, alias rules and evidence handoff](decision-evidence/34-media-identity-contract.md)
for `MediaIdentity` and release dataclasses, Arr/Jellyfin adapters, allowlisted
capture sanitizer, real `media-identity-captures-v1.json`, strict confirmation
models/API, frontend types/editor and migration `0004_media_identity`.
`media_items.confirmed_identity_json` is nullable with no inferred backfill.
Full plain/encrypted backups preserve it; settings-only retains its existing
no-local-media policy. Fresh/upgrade/packaged schema admission is updated.

Decisions: G-MATCHING's typed IDs/compatibility-first direction only; no new
fingerprint or duration tolerance selected. The user explicitly approved verified
backup and workspace 0001-to-0003, then 0003-to-0004 upgrades. The latter fresh
snapshot `snapshot-c9127afdb094428a8b50f2a22afede73` independently validated;
active schema and completed journal verified. No wipe or unknown-schema stamp.

Verification commands/results:

- `.venv/bin/python -m pytest -q tests/test_media_identity.py tests/test_auth_policy.py tests/test_editarr_api_settings.py tests/test_editarr_catalog.py tests/test_editarr_settings.py tests/test_editarr_library.py tests/test_fresh_migrations.py tests/test_existing_migrations.py tests/test_migration_packaging.py tests/test_migration_preflight.py tests/test_backup_restore.py tests/test_backup_activation.py tests/test_version_info.py`: **184 passed**.
- `.venv/bin/ruff format` on the 19 touched identity/integration/migration/test
  files applied formatting; `.venv/bin/ruff check` on those paths passed.
- `pnpm exec vitest run --maxWorkers=1` in `web`: **174 passed**, 24 files.
  `pnpm exec tsc --noEmit` and `pnpm build` passed.
- `pnpm exec playwright test e2e/media-identity.spec.ts e2e/app-layout.spec.ts e2e/app-review.spec.ts`: **22 passed**. Identity checks include 320/1440px,
  axe, overflow, explicit/cancelled writes, focus restoration and saved reload.
- After scoped visual polish, the identity unit **3**, browser **2** and build
  checks passed again; mobile screenshots were visually inspected.
- Focused editor diagnostics report no errors. Documentation links/fences and
  final whitespace are checked at closeout.

Limits/recovery: Live captures preserve types/equality within each provider,
not cross-provider linkage; real shared-file double episodes were not observed
and remain separately synthetic tests. Upstream episode aliases are deliberately
unverified/omitted; explicit linked series plus numbering or user-confirmed IDs
are actionable alternatives. Audio is unknown without selected-track evidence;
manual confirmation does not fabricate runtime. Jellyfin `IndexNumberEnd`
expansion is not adopted. No production matching/publication/deployment or
cross-platform native UI check occurred. Workspace connections/data are retained;
restore the verified snapshot through the existing recovery workflow if needed.

Remaining work in this task: None. Local development app is restarted at
`http://localhost:5178/`; no scan/model download is triggered by this work.

Next task: CSS-35. Read this evidence and G-MATCHING, then prepare bounded
sampling-layout benchmark candidates for explicit approval before selecting or
implementing the community SHA-256 fingerprint protocol. Preserve legacy MD5.

Direct dependent tasks: [CSS-35](35-file-fingerprints.md), [CSS-36](36-compatibility-policy.md), [CSS-37](37-donation-artifact-builder.md), [CSS-48](48-local-lookup-adapter.md).
