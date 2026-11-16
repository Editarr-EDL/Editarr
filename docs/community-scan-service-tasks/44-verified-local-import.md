# CSS-44 — Verify and import downloaded community evidence atomically

> Runtime update (October 7, 2026): Node/SQLite/Docker is the only build, test
> and deployment path. Use the [current runtime guide](../community-service-docker.md).
> Provider-specific commands, bindings and measurements below are historical, not
> current instructions. Retained SQL filenames, accounting keys and archive formats
> are compatibility data. Cloudflare Tunnel/Access remains ingress/operator authentication.

Status: **DONE locally** on October 3, 2026. Production remains disabled; no existing
installation database was migrated and no live service was contacted.

- Prerequisites: [CSS-13](13-artifact-signatures.md), [CSS-21](21-local-community-state.md), [CSS-42](42-artifact-download-service.md), [CSS-43](43-import-repository-refactor.md), [CSS-29](29-local-service-client.md)
- Decision gates: None beyond the shared rules.
- Parent sections: contracts §6; main §7.
- Read first: [execution guide](README.md), [shared context and definition of done](context.md), the named [decision gates](decisions.md), then predecessor handoffs.
- Parent sources: [main plan](../community-scan-service-plan.md), [contracts](../community-scan-service-contracts.md), [implementation](../community-scan-service-implementation.md), [Discord design](../community-scan-service-discord-enrollment.md).

## Context and outcome

Remote data is untrusted and may not fit the target cut. Import preserves immutable source evidence; review mapping is a separate explicit action.

## Starting points

`scanner/integrations/community/downloads.py`,
`scanner/persistence/repository/community_imports.py`, and scan/review/export provenance serializers.

Paths above are repository-relative inspection targets; new paths are proposals.
Use the predecessor's actual module map if implementation moved a responsibility.
Preserve existing public facades and unrelated work.

## Work within this task

1. Verify byte digests, durable signature/trusted key binding, schema, limits and source timeline before DB writes.
2. Commit scan plus remote revision/digest/author/lineage provenance atomically, deduplicating by service/revision/digest.
3. Stage a pending receipt only after successful commit; preserve independent success and reject any remote local path/ID/command.

Do not implement downstream features in this task. If a missing dependency or major
choice appears, record it and ask the user; complete independent authorized work first.

## Acceptance criteria

- [x] Retry or restore reuses prior import and associations without duplicate scans.
- [x] Corrupt/unsupported/incompatible artifact leaves local state untouched; uncertain mapping requires explicit review.
- [x] Original attribution/signatures survive review/export and derived sharing; no double retiming or saved-review overwrite.

## Verification

- Test hostile artifacts, retired historical signing keys, crash before/after commit and duplicate import.
- Run EDL/MCF/review/export regressions and provenance round trips.
- Use the [verification commands and rules](context.md#verification) for touched areas.
  Record actual commands, outcomes, and unavailable checks; do not claim unrun tests passed.

## Definition of done

- [x] Every acceptance criterion above is met, and relevant verification passed.
- [x] Applicable decision gates have explicit recorded answers; no invented product policy.
- [x] Shared [completion requirements](context.md#definition-of-done) are satisfied.
- [x] Update this status and the [master checklist](README.md); do not mark a partial task done.
- [x] Write the handoff summary below, including evidence and the next exact action.
      **Required task-specific handoff:** Provide import/reuse results, receipt outbox hook and immutable-source review integration to CSS-50/51/62.

## Handoff summary for the next task

### Implemented contract

GitHub Copilot completed the local/mock implementation October 3; changes are
uncommitted. The stable legacy EDL/MCF facades remain unchanged.

- `scanner/security/community_artifact_signatures.py` verifies the original three
  buffers, exact envelope and author/submission binding; active/retired historical
  author keys verify, revoked/compromised keys fail. Original bytes are never rebuilt.
- `scanner/security/community_download_receipts.py` verifies the existing CSS-42
  binary framing, stable identity, canonical base64 and separately trusted key ring.
  The shared Node/Worker receipt fixture is unchanged; no expiry or new signing format.
- Signed `GET /v1/receipt-keys` returns exactly `{version:1, service, keys}` with
  the configured public receipt ring, exact `Content-Length`/`Content-Digest`, no
  `Content-Encoding`, and private/no-store/no-transform caching. It uses the same
  native admission, current reader/member and trusted ingress checks as downloads;
  it never returns private keys or increments import counts.
- `downloads.fetch_verified_download` refreshes that history on every call and
  fails closed without stale fallback. It verifies the outer response and original
  buffers under explicit resource/response bounds. The root contribution ID comes
  from the caller's authenticated catalog selection, not from artifact assertions.
- `downloads.download_and_import` is the backend orchestration entry point for
  CSS-50. Use a clean, caller-owned Session and independently resolved
  `LocalImportTarget(media_item_id, media_source_id, resolved_local_path, evidence)`.
  The lower-level `import_verified_download` adapter is for already freshly verified
  inputs, not a way to bypass service key-status refresh. Neither entry point is a
  new local HTTP route or an automatic download/enrollment trigger.
- Compatibility uses the approved CSS-36 core independently for active channels.
  Incompatible artifacts fail; uncertainty requires `confirm_uncertain=True`.
  V1 cannot fabricate independent stream or measured-duration evidence. Cue times stay on the artifact's
  source/already-derived timeline, with nullable confidence and honest remote stages.
  No retiming, saved-review replacement, model execution or sidecar write occurs at import.

### Persistence and recovery

Additive local migration `0006_community_evidence` follows `0005_audio_tracks`.
It adds nullable artifact/manifest/signature BLOBs and historical-key, public
provenance, event-ID and original-receipt JSON to `community_imports`, plus
`community_import_targets` keyed by import/media/version with the approved local
path/hash, target evidence and review-required flag. Legacy rows stay unknown; a
matching legacy record without exact evidence fails closed rather than fabricating
verification or creating another scan. Downgrade cannot discard signed evidence.
Supported unversioned and 0001-0005 upgrades preserve existing rows and verified
recoverable snapshots. Applying this migration to an existing installation still
requires its ordinary authorized upgrade and backup procedure.

One transaction commits locally generated scan/finding IDs, exact evidence,
author/lineage/event bindings and the target association. Service/revision/digest
deduplication, including concurrent retries and restored databases, reuses the
original scan. Association retries reject changed path/evidence intent. Review
create/save/export uses only explicitly verified target associations, retaining
ordinary file/path/hash guards for legacy/generated scans. EDL now uses the verified
target path, matching MCF. Canonical `both` cues work in audio/video/both roles without
reclassifying the immutable source.
Editable review bounds use an independently measured (`file_probe`) approved local
target runtime when available, without changing the source scan duration. Unknown
target measurements retain the existing conservative fallback. Explicitly mapped
review copies may therefore fit a different target runtime and export normally;
the original source cues and signatures remain unchanged.

`CommunityImportResult` exposes `scan`, `import_id`, `reused`, target media/version,
`compatibility`, `review_required`, and `receipt_state`. The review-required flag
describes matching uncertainty, not whether a saved review has already been edited.
Safe `community_provenance` is present on scan/review/export references; raw signed
buffers, key-history snapshots, local associations and personalized receipts are
not public DTO fields. Derived sharing retains original revision/digest/author and
remote event IDs; its private capture excludes signed BLOBs, receipts and key history.
Original signatures remain in the database, not embedded in legacy EDL/MCF text.

Only after the import commit, `queue_import_receipt(db, import_id)` stages the original
receipt under stable operation ID `import-<receiptId>`; the caller commits that
separate outbox transaction. A failure rolls back only receipt staging and returns
successful import with `receipt_state='unavailable'`. Retry reuses the saved original
receipt even if the service issues a later timestamp/signature. A crash after import
commit but before queueing is recoverable from the retained `receipt_json`; invoke
the hook for an already committed import in a clean recovery Session. Existing
submitted/failed/acknowledged intents retain their state and exact identity.
CSS-51 still owns sending, current authorization and counting under G-REPUTATION.
No acknowledgment, popularity or reputation mutation was implemented here.

Full backups retain all signed evidence, target associations and original receipt
intents. Settings-only backups exclude them. Plain and age-scrypt encrypted restore
previews preserve that distinction without activating the target installation.

### Verification

The combined local regression command below finished with **886 passed, 8 failed**.
All eight failures are existing `test_scan_naming_core.py` creation fixtures requesting
audio on empty media without selecting a track (409 rather than 201); a representative
`[None-False-manual]` case reproduced identically against a temporary export of
committed HEAD. The admission code and those tests were not changed. No failure is
hidden by an exclusion or expectation change.

```sh
.venv/bin/python -m pytest -q tests/test_community*.py tests/test_edl.py tests/test_mcf*.py tests/test_editarr_reviews.py tests/test_scan_naming*.py tests/test_review_repository*.py tests/test_review_retime*.py tests/test_fresh_migrations.py tests/test_existing_migrations.py tests/test_migration_preflight.py tests/test_migration_packaging.py tests/test_version_info.py tests/test_backup*.py tests/test_portable_backup.py
```

Additional focused results: 38 populated-upgrade/four-mode restore checks passed;
16 v1/signature and installed-wheel/sdist/startup checks passed; 72 final role,
review/save/integrity/retiming checks passed; 54 final download/import/signature
checks passed, including all eight frozen v2 variants, retired/revoked keys,
hostile DTOs, fresh-history failures, before/after-commit crashes, changed issuance,
four concurrent retries, restored reuse and independent receipt failure.
After the final source/target runtime separation, **312 shared import/review/export/
retiming and installed-package tests passed**. The v1 regression explicitly maps
editable copies onto a measured target, supplies missing review classification,
and exports MCF without changing source duration or attribution.

```sh
pnpm --dir community-service exec vitest run --reporter=dot
pnpm --dir community-service typecheck
pnpm --dir community-service build
```

All **491 Worker tests / 74 files** passed, typecheck passed, dry-run bundle
738.37 KiB / gzip 127.76 KiB. Wrangler's existing `@types/node` advisory remains;
no dependency was added. Changed-file Ruff and editor diagnostics passed; six touched
documents passed 166 local-link, balanced-fence and task/index status checks, and
`git diff --check` passed. No real service/Discord, existing DB,
user media, model download, frontend, sidecar, secret provisioning or deployment
was exercised; hosted CPU/billing and other-platform execution are not verified.

### Next Actions

No CSS-44 implementation remains. CSS-50 must use the backend orchestrator, resolve
the physical target on the backend, preserve explicit uncertain-review approval and
handle successful import with unavailable receipt independently. CSS-51 starts from
the committed import ID/original receipt hook, then resolves G-REPUTATION before
implementing acknowledgment/counting. CSS-62 must preserve immutable original
buffers/event attribution when reviewing, explicitly retiming or deriving new shares.

The next eligible numbered task is [CSS-45](45-local-publication-outbox.md): read
CSS-21/29/37/39 handoffs and the current D1-only contract, then inspect
`donations.approve_donation` and `repository_community.enqueue_operation` to persist
the exact approved publication buffers before any network work. Ask before any
missing product or deployment decision; this task does not authorize live operation.

Direct dependent tasks: [CSS-50](50-find-filters-downloads.md), [CSS-51](51-import-receipts.md).

