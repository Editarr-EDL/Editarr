# Editarr Verification Map

This is the maintained user-path map for the real source-development application.
Read the [skill](../SKILL.md), launch a run-owned instance, and run doctor before
driving. Authentication and the Jellyfin item review/import recipe have automated
journeys in this helper. Other entries have explicit fixture requirements;
their presence does not claim execution.

## Baseline Preconditions

- Start from an empty temporary data directory using `scripts/verify.mjs`.
- Use only its printed URL, process group, and `instance.json` paths.
- Complete administrator creation through the UI; no auth bypass or direct DB seed.
- No provider, media, scans, reviews, or models are configured initially.
- Reuse the repository's installed Playwright; never reuse its default shared server.
- Use 1440x900 and 320x900 viewports for changed browser workflows.

## Driving Conventions

- Start from baseline unless a feature lists additional prerequisites.
- Prefer roles and accessible names. Scope duplicate buttons to their panel/dialog.
- Manual recipes use a run-owned `page`; `origin`, `data`, and `evidence` come from
  that run's printed artifact directory and `instance.json`.
- Use synthetic local inputs and approved production-boundary test doubles only.
- Missing media, models, credentials, or providers make that entry point BLOCKED.
  Record the actual attempted step and missing prerequisite; do not invent a pass.
- Never use the user's real media for import/export, which writes adjacent sidecars.
- Cleanup scratch and owned processes, not evidence or another run.

## Proof And Coverage

Record feature ID, entry point, actions, requests/statuses, visible result, and
side effects. Capture screenshot plus ARIA snapshot before and after actions.
Reopen persisted state or independently read protected APIs/sidecar/archive files.
Preserve failures; never count a mocked UI spec as a real backend journey.

Every media item comes from Jellyfin. The labelled synthetic transport in
`scripts/lib/synthetic-jellyfin.mjs` stands in for it in the automated journeys;
it is not live Jellyfin evidence.
It pages listings (`StartIndex`, `Limit`), filters by `IncludeItemTypes`, answers
per-user `/Items/{id}` reads, and relays user routes to the run's Jellyfin, so
Jellyfin members can read its catalog. Closing it writes
`synthetic-jellyfin-calls.json` with each request's method, path, query names,
and status.
The initial smoke proves authentication only; all other rows remain unexecuted
until their recipes run with the listed prerequisites.

## Features

- [Authentication](./authentication.md): first-run setup, deep links, sign-out,
  and persisted sign-in; automated with `verify.mjs auth`, `pair`, and
  `auth --inject-failure`.
- [Import filters](./imports.md): EDL/MCF upload import, timing,
  and a resulting saved review. `auth --reviews` automates the uploads on a
  synthetic Jellyfin item.
- [Saved reviews and exports](./reviews-exports.md): manual findings, independent
  saved versions, canonical personal EDL/MCF sidecars and matching downloads.
  `auth --reviews` uses writable disposable media and preserves legacy files.
  It also covers current-owner policy, stale refusals,
  independent stored-value checks, enforcement removal, retained conflict
  drafts, personal filter modes, and automatic personal regeneration with
  matching disk, download, snapshot, and system-audit evidence, then review
  deletion with per-file retain/remove choices and hidden-file nondisclosure,
  then the export dialog, replacement confirmation with stale recovery,
  owner targets, cross-user deletes, account switches, and the review deletion
  dialog against parsed disk files.
  `scan-ui.mjs --reviews` covers personal imports, timing, completed
  chapter-based credits, copy, rename, delete, and private-route refusals
  for Jellyfin.
  `auth --plugin-interop` proves the public plugin serves each linked user's
  published EDL and MCF bytes in a live Jellyfin 12.0.0 container.
- [Deleted scan recovery](./review-repairs.md): snapshot-backed orphan upgrades,
  flagged lists, resolution, manual cues, delete pre-checks, and 409/403 probes.
  Automated with `repairs --ports=A-B` at 1440 and 320 with axe.
- [Catalog scans and queue access](./scans.md): ordinary-user submissions,
  own actions, private-reference blockers, path-safe worker diagnostics, and
  principal-independent media revocation. `scan-ui.mjs`
  automates a synthetic Jellyfin provider and fake models. Real inference needs
  separately approved ready artifacts.
- [Backups](./backups.md): full archive export from Settings and the
  four-instance restore journey (`verify.mjs restore`): onboarding restore into an
  empty instance, CLI restore after an interrupted activation, and named refusals.
  It also lists the upgrade matrix and the installed restore, owner recovery,
  and TLS proxy checks.
- [Settings access](./settings-access.md): owner, admin, and user settings,
  denied operations, standalone restart, scratch model operations, an archive
  download with no restore control, and enforced-filter authentication at 1440
  and 320 pixels.
  The recipe records the separate post-edit archive validation limitation.
- [Multi-user journeys](./multi-user.md): account and profile recipes, role,
  media, filter policy, bulk, and ownership controls, with each sub-feature's
  account workflows and verification commands.
- [Multi-user permissions](./multi-user-permissions.md): evaluator tables,
  test-fixture HTTP checks, and the route coverage check that every gated
  handler resolves its principal. Every launch admits every active account;
  plain `auth` drives the real two-user smoke.
- [Jellyfin users](./jellyfin-users.md): verified Jellyfin identity, deletion,
  outage, and item-access checks against a synthetic local boundary, and checks for
  account admission and sync (first sign-in, import, five-minute and manual
  sync, 24-hour outage grace), with live Jellyfin 12 checks for both.
- [Account linking](./account-linking.md): proof-bound linking and relinking
  with reviewed personal-export moves; `auth --reviews --account-link` proves a
  linked member gets no link action and blocked proofs move nothing, then links a
  seeded local member that 0030 disabled, moving its EDL and MCF through a
  duplicate, a forged target, and a write failure, and `restore` links a restored
  unlinked account.
- [Account deletion](./account-deletion.md): retention, transfer, and permanent
  deletion with a confirmed impact, replay, and bulk results, through the API and
  the Users UI; `auth --community --reviews
  --account-deletion` deletes synthetic members, including one with community
  history, and resolves a duplicate identity for a seeded 0030-disabled account.
- [Media access](./media-access.md): Jellyfin media decisions and local
  scopes, checked against the synthetic boundary and real accounts. Catalog routes use the same checks.
- [Catalog access](./catalog-access.md): catalog, artwork, candidate, and track
  routes decided per user before counts and paging, over HTTP with recorded
  boundaries and against live Jellyfin 12 with `catalog_live.py run`.
- [Identity isolation](./identity-isolation.md): owner, admin, and four
  Jellyfin users with different library policies, checked for counts,
  errors, artwork, storage, deep links, keys, and in-session changes against a
  labelled pinned Jellyfin; automated with `auth --identity`. Its HTTP clock
  tests cover owner immunity, the 24-hour limit, five-minute sync, and
  fresh-authentication refusals.
- [Audit history](./audit-history.md): scoped admin and owner list/detail,
  ordinary-user denial, capability removal, retention refresh, and account
  isolation at 1440 and 320. Automated with `auth --audit`.
- [Archive](./archive.md): read-only records of removed Radarr, Sonarr, and
  Other Files sources after the `0025_archive_arr_sources` and
  `0026_archive_manual_files` upgrades, owner view at 1440 and 320,
  ordinary-user refusal, no actions, and a refused orphan import. Manual
  recipe; `verify.mjs upgrade` proves the Other Files row through the API, the
  orphan import refusal, and the owner view at 1440 and 320 in its second run,
  whose owner is linked to Jellyfin before startup.
- [Community activities](./community-activities.md): actor and contributor isolation,
  exact previews, receipts, votes and lock-time refusal. The
  `auth --community --ports=9100-9119` drives personal
  and named-account UI against the existing signature-verifying transport double,
  including acting/original attribution, stale refresh, axe and overflow. The existing
  `auth --reviews` journey includes real local community
  status and capability revocation. Signed service behavior uses the established double.
